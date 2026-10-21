import io
import re
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(
    page_title="전국 고령화 단계구분도",
    page_icon="🗺️",
    layout="wide",
)

st.title("🗺️ 전국 고령화 단계구분도")
st.caption("전국 시군구의 연도별 인구 구조를 비교합니다.")

POPULATION_URL = (
    "https://raw.githubusercontent.com/first2022-cyber/"
    "contents//main/population_ts_yearly_gender.zip"
)
GEOJSON_URL = (
    "https://raw.githubusercontent.com/first2022-cyber/"
    "contents//main/sigungu_kr.geojson"
)

# 고령화율 구간: 연도와 관계없이 고정
AGING_BREAKS = [19, 23, 28, 38]
AGING_MAX = 50

# 유소년 비율은 별도의 고정 구간 사용
YOUTH_BREAKS = [5, 8, 11, 14]
YOUTH_MAX = 20

# 낮은 비율에서 높은 비율 순서
COLORS = ["#fff7bc", "#fec44f", "#fe9929", "#ec7014", "#993404"]
MISSING_COLOR = "#d3d3d3"


def normalize_code(value):
    """행정구역 코드를 5자리 문자열로 정규화한다."""
    if pd.isna(value):
        return None

    code = str(value).strip()

    # CSV/GeoJSON에 숫자형 코드가 소수점으로 들어온 경우 처리
    if re.fullmatch(r"\d+\.0", code):
        code = code[:-2]

    if code.isdigit():
        code = code.zfill(5)

    return code


def convert_old_code(code):
    """과거 행정구역 코드를 현재 경계 코드에 맞춘다."""
    code = normalize_code(code)

    if code is None:
        return None

    # 군위군: 47720 -> 27720
    if code == "47720":
        return "27720"

    # 과거 강원도 코드 42 -> 현재 경계 코드 51
    if code.startswith("42") and len(code) == 5:
        return "51" + code[2:]

    # 과거 전라북도 코드 45 -> 현재 경계 코드 52
    if code.startswith("45") and len(code) == 5:
        return "52" + code[2:]

    return code


@st.cache_data(show_spinner="인구 데이터를 불러오는 중...")
def load_population():
    response = requests.get(POPULATION_URL, timeout=120)
    response.raise_for_status()

    df = pd.read_csv(
        io.BytesIO(response.content),
        compression="zip",
        dtype={"코드": "string"},
        low_memory=False,
    )

    required = {"연도", "시도", "시군구", "동", "코드"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"필수 열이 없습니다: {sorted(missing)}")

    df["연도"] = pd.to_numeric(df["연도"], errors="coerce")
    df["원본코드"] = df["코드"].map(normalize_code)
    df["시군구코드"] = df["원본코드"].map(
        lambda code: convert_old_code(code[:5]) if code else None
    )

    total_cols = [
        col for col in df.columns if col.startswith("계_")
    ]

    if not total_cols:
        raise ValueError("'계_'로 시작하는 인구 열이 없습니다.")

    # 0~14세 및 65세 이상 열을 이름 규칙으로 찾는다.
    youth_cols = []
    elderly_cols = []

    for col in total_cols:
        match = re.fullmatch(r"계_(\d+)세", col)

        if match:
            age = int(match.group(1))
            if 0 <= age <= 14:
                youth_cols.append(col)
            if age >= 65:
                elderly_cols.append(col)

        elif col == "계_100세 이상":
            elderly_cols.append(col)

    if not youth_cols:
        raise ValueError("0~14세 인구 열을 찾지 못했습니다.")
    if not elderly_cols:
        raise ValueError("65세 이상 인구 열을 찾지 못했습니다.")

    # 모든 계_ 열을 전체 인구 계산에 사용한다.
    # 남_ 및 여_ 열은 계산에 더하지 않는다.
    for col in total_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["전체인구"] = df[total_cols].sum(axis=1)
    df["유소년인구"] = df[youth_cols].sum(axis=1)
    df["고령인구"] = df[elderly_cols].sum(axis=1)

    # 시군구 단위로 집계. 연도는 아직 필터링하지 않아
    # 슬라이더로 모든 제공 연도를 선택할 수 있다.
    grouped = (
        df.dropna(subset=["연도", "시군구코드"])
        .groupby(["연도", "시군구코드"], as_index=False)
        .agg(
            전체인구=("전체인구", "sum"),
            유소년인구=("유소년인구", "sum"),
            고령인구=("고령인구", "sum"),
        )
    )

    grouped = grouped[grouped["전체인구"] > 0].copy()

    grouped["고령화율"] = (
        grouped["고령인구"] / grouped["전체인구"] * 100
    )
    grouped["유소년비율"] = (
        grouped["유소년인구"] / grouped["전체인구"] * 100
    )

    grouped["연도"] = grouped["연도"].astype(int)
    grouped["시군구코드"] = grouped["시군구코드"].astype(str)

    return grouped


@st.cache_data(show_spinner="시군구 경계 지도를 불러오는 중...")
def load_geojson():
    response = requests.get(GEOJSON_URL, timeout=120)
    response.raise_for_status()
    geojson = response.json()

    if geojson.get("type") != "FeatureCollection":
        raise ValueError("GeoJSON이 FeatureCollection 형식이 아닙니다.")

    # GeoJSON 코드도 문자열로 정규화한다.
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        props["코드"] = normalize_code(props.get("코드"))

    return geojson


def make_step_scale(breaks, maximum, colors):
    """연속 색상표에 계단식 구간 경계를 만든다."""
    points = [0] + breaks + [maximum]
    scale = []

    for i, color in enumerate(colors):
        left = points[i] / maximum
        right = points[i + 1] / maximum
        scale.append([left, color])
        scale.append([right, color])

    return scale


def make_map(geojson, data, metric, breaks, maximum, year, province):
    # 시도 필터를 GeoJSON과 통계 데이터 양쪽에 적용
    features = geojson["features"]

    if province != "전국":
        features = [
            feature for feature in features
            if feature.get("properties", {}).get("시도") == province
        ]

    filtered_geojson = {
        "type": "FeatureCollection",
        "features": features,
    }

    geo_codes = {
        feature["properties"]["코드"]
        for feature in features
        if feature.get("properties", {}).get("코드") is not None
    }

    view_data = data[data["시군구코드"].isin(geo_codes)].copy()

    # 경계 코드 기준으로 데이터가 없는 지역은 지도에서 회색 처리
    available_codes = set(view_data["시군구코드"])
    missing_codes = sorted(geo_codes - available_codes)

    step_scale = make_step_scale(breaks, maximum, COLORS)

    fig = px.choropleth(
        view_data,
        geojson=filtered_geojson,
        locations="시군구코드",
        featureidkey="properties.코드",
        color=metric,
        range_color=(0, maximum),
        color_continuous_scale=step_scale,
        custom_data=["시군구코드", metric],
        labels={metric: f"{metric}(%)"},
    )

    fig.update_traces(
        marker_line_color="white",
        marker_line_width=0.45,
        hovertemplate=(
            "<b>%{location}</b><br>"
            f"{metric}: %{{z:.2f}}%"
            "<extra></extra>"
        ),
    )

    # 데이터가 없는 경계는 별도의 회색 레이어로 표시
    if missing_codes:
        fig.add_trace(
            go.Choropleth(
                geojson=filtered_geojson,
                locations=missing_codes,
                z=[0] * len(missing_codes),
                featureidkey="properties.코드",
                colorscale=[
                    [0, MISSING_COLOR],
                    [1, MISSING_COLOR],
                ],
                showscale=False,
                marker_line_color="white",
                marker_line_width=0.45,
                hovertemplate=(
                    "코드: %{location}<br>"
                    "자료 없음 또는 코드 불일치"
                    "<extra></extra>"
                ),
                name="자료 없음",
            )
        )

    tickvals = [0] + breaks + [maximum]
    ticktext = [f"{v}%" for v in tickvals]

    fig.update_layout(
        height=760 if province == "전국" else 650,
        margin=dict(l=0, r=0, t=10, b=0),
        paper_bgcolor="white",
        plot_bgcolor="white",
        coloraxis_colorbar=dict(
            title=f"{metric}(%)",
            tickvals=tickvals,
            ticktext=ticktext,
            len=0.75,
        ),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=0.01,
            xanchor="left",
            x=0,
        ),
    )

    fig.update_geos(
        visible=False,
        fitbounds="locations",
        showcountries=False,
        showcoastlines=False,
        showland=False,
        showlakes=False,
        showrivers=False,
        bgcolor="white",
    )

    return fig, missing_codes, len(geo_codes)


try:
    population = load_population()
    geojson = load_geojson()

    if population.empty:
        st.error("계산할 수 있는 인구 자료가 없습니다.")
        st.stop()

    years = sorted(population["연도"].unique().tolist())

    # -----------------------------
    # 필터
    # -----------------------------
    st.subheader("조회 조건")

    control1, control2, control3 = st.columns([1, 1, 2])

    with control1:
        year = st.slider(
            "기준 연도",
            min_value=int(min(years)),
            max_value=int(max(years)),
            value=int(max(years)),
            step=1,
        )

    with control2:
        metric_label = st.selectbox(
            "표시 지표",
            ["고령화율 (65세 이상)", "유소년 비율 (0~14세)"],
        )

    metric = (
        "고령화율"
        if metric_label.startswith("고령화율")
        else "유소년비율"
    )

    if metric == "고령화율":
        breaks = AGING_BREAKS
        maximum = AGING_MAX
        metric_title = "고령화율"
    else:
        breaks = YOUTH_BREAKS
        maximum = YOUTH_MAX
        metric_title = "유소년 비율"

    # GeoJSON의 시도 속성을 사용해 선택 목록 구성
    provinces = sorted({
        feature.get("properties", {}).get("시도")
        for feature in geojson["features"]
        if feature.get("properties", {}).get("시도")
    })

    with control3:
        province = st.selectbox(
            "지도 범위",
            ["전국"] + provinces,
            index=0,
        )

    # 선택 연도 자료
    year_data = population[population["연도"] == year].copy()

    if year_data.empty:
        st.warning(f"{year}년 자료가 없습니다.")
        st.stop()

    # 전국 지표 카드: 시군구별 비율의 평균이 아니라 인구 합계로 계산
    total_population = year_data["전체인구"].sum()

    if metric == "고령화율":
        total_selected = year_data["고령인구"].sum()
    else:
        total_selected = year_data["유소년인구"].sum()

    national_rate = (
        total_selected / total_population * 100
        if total_population > 0 else float("nan")
    )

    # 최고·최저는 현재 선택 지표 기준으로 전국에서 계산
    geo_name = {
        feature["properties"]["코드"]:
        feature["properties"].get("시군구", "이름 없음")
        for feature in geojson["features"]
        if feature.get("properties", {}).get("코드") is not None
    }

    year_data["시군구명"] = year_data["시군구코드"].map(geo_name)
    nationwide = year_data.dropna(subset=["시군구명"]).copy()

    if nationwide.empty:
        st.warning("인구 데이터와 경계 코드가 일치하는 지역이 없습니다.")
        st.stop()

    highest = nationwide.loc[nationwide[metric].idxmax()]
    lowest = nationwide.loc[nationwide[metric].idxmin()]

    # -----------------------------
    # 지표 카드 3개
    # -----------------------------
    card1, card2, card3 = st.columns(3)

    card1.metric(
        f"전국 {metric_title}",
        f"{national_rate:.2f}%" if pd.notna(national_rate) else "자료 없음",
    )
    card2.metric(
        "가장 높은 시군구",
        str(highest["시군구명"]),
        f"{highest[metric]:.2f}%",
    )
    card3.metric(
        "가장 낮은 시군구",
        str(lowest["시군구명"]),
        f"{lowest[metric]:.2f}%",
    )

    st.subheader(f"{year}년 {province} {metric_title} 단계구분도")

    if metric == "고령화율":
        st.caption(
            "고정 구간: 19% · 23% · 28% · 38%. "
            "연도를 바꿔도 같은 구간으로 비교합니다."
        )
    else:
        st.caption(
            "유소년 비율 전용 고정 구간: "
            "5% · 8% · 11% · 14%. "
            "고령화율과 다른 색 구간을 사용합니다."
        )

    year_data["시군구코드"] = year_data["시군구코드"].astype(str)

    fig, missing_codes, boundary_count = make_map(
        geojson=geojson,
        data=year_data,
        metric=metric,
        breaks=breaks,
        maximum=maximum,
        year=year,
        province=province,
    )

    st.plotly_chart(fig, use_container_width=True)

    if missing_codes:
        st.info(
            f"회색 지역은 해당 연도 인구 자료가 없거나 경계 코드가 "
            f"일치하지 않는 지역입니다. 현재 지도 범위에서 "
            f"{len(missing_codes)}개 지역이 회색으로 표시됩니다. "
            "행정구역 개편이나 자료 누락으로 인해 발생할 수 있습니다."
        )
    else:
        st.caption("현재 지도 범위에서 코드 불일치 지역이 발견되지 않았습니다.")

    # -----------------------------
    # 표
    # -----------------------------
    with st.expander("시군구별 지표 표 보기"):
        table = year_data.copy()
        table["시군구"] = table["시군구코드"].map(geo_name)
        table = table.dropna(subset=["시군구"])
        table = table[
            ["시군구코드", "시군구", "전체인구",
             "유소년인구", "고령인구", "유소년비율", "고령화율"]
        ].sort_values(metric, ascending=False)

        table = table.rename(columns={
            "시군구코드": "코드",
            "전체인구": "전체 인구",
            "유소년인구": "0~14세 인구",
            "고령인구": "65세 이상 인구",
            "유소년비율": "유소년 비율(%)",
            "고령화율": "고령화율(%)",
        })

        st.dataframe(
            table.style.format({
                "전체 인구": "{:,.0f}",
                "0~14세 인구": "{:,.0f}",
                "65세 이상 인구": "{:,.0f}",
                "유소년 비율(%)": "{:.2f}",
                "고령화율(%)": "{:.2f}",
            }),
            use_container_width=True,
            hide_index=True,
        )

except requests.RequestException as e:
    st.error(f"데이터를 내려받지 못했습니다: {e}")
except Exception as e:
    st.error(f"앱 실행 중 오류가 발생했습니다: {e}")
    st.caption(
        "오류가 계속되면 GitHub의 CSV 열 이름, GeoJSON 속성, "
        "코드 형식을 확인해주세요."
    )
