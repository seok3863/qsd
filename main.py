import json
import io
import re
import requests
import pandas as pd
import plotly.express as px
import streamlit as st

# -----------------------------
# 페이지 설정
# -----------------------------
st.set_page_config(
    page_title="전국 고령화 단계구분도",
    page_icon="🗺️",
    layout="wide"
)

st.title("🗺️ 전국 고령화 단계구분도")
st.markdown(
    "2026년 6월 기준 전국 시군구별 **65세 이상 인구 비율**을 지도에서 확인합니다."
)

DATA_URL = (
    "https://raw.githubusercontent.com/"
    "first2022-cyber/contents/main/population_ts_yearly_gender.zip"
)
GEOJSON_URL = (
    "https://raw.githubusercontent.com/"
    "first2022-cyber/contents/main/sigungu_kr.geojson"
)


# -----------------------------
# 데이터 불러오기
# -----------------------------
@st.cache_data(show_spinner="인구 데이터를 불러오는 중...")
def load_population():
    response = requests.get(DATA_URL, timeout=120)
    response.raise_for_status()

    # ZIP 압축 CSV를 pandas로 읽기
    df = pd.read_csv(
        io.BytesIO(response.content),
        compression="zip",
        dtype={"코드": "string"},
        low_memory=False
    )

    required = {"연도", "시도", "시군구", "동", "코드"}
    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"인구 데이터에 필요한 열이 없습니다: {', '.join(sorted(missing))}"
        )

    # 연도는 숫자로 변환
    df["연도"] = pd.to_numeric(df["연도"], errors="coerce")

    # 2026년 자료만 사용
    df = df.loc[df["연도"] == 2026].copy()

    if df.empty:
        raise ValueError("인구 데이터에서 2026년 자료를 찾지 못했습니다.")

    # 코드는 문자열로 처리하고 앞의 5자리를 시군구 코드로 사용
    df["코드"] = df["코드"].astype("string").str.strip()
    df["시군구코드"] = df["코드"].str[:5]

    # 계_로 시작하는 연령별 인구 열만 선택
    total_cols = [
        col for col in df.columns
        if col.startswith("계_")
    ]

    if not total_cols:
        raise ValueError("'계_'로 시작하는 인구 열을 찾지 못했습니다.")

    # 65세 이상 열: 계_65세부터 계_100세 이상까지
    elderly_cols = []

    for col in total_cols:
        match = re.fullmatch(r"계_(\d+)세", col)

        if match and int(match.group(1)) >= 65:
            elderly_cols.append(col)
        elif col == "계_100세 이상":
            elderly_cols.append(col)

    if not elderly_cols:
        raise ValueError("65세 이상 인구 열을 찾지 못했습니다.")

    # 숫자형 변환
    for col in total_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    # 읍면동별 65세 이상 인구 및 전체 인구
    df["고령인구"] = df[elderly_cols].sum(axis=1)
    df["전체인구"] = df[total_cols].sum(axis=1)

    # 시군구 코드별 합산
    result = (
        df.groupby("시군구코드", as_index=False)
        .agg(
            고령인구=("고령인구", "sum"),
            전체인구=("전체인구", "sum")
        )
    )

    result = result.loc[result["전체인구"] > 0].copy()
    result["고령화율"] = (
        result["고령인구"] / result["전체인구"] * 100
    )

    # 앞자리 0이 사라지지 않도록 문자열 유지
    result["시군구코드"] = result["시군구코드"].astype("string")

    return result


@st.cache_data(show_spinner="시군구 경계 지도를 불러오는 중...")
def load_geojson():
    response = requests.get(GEOJSON_URL, timeout=120)
    response.raise_for_status()

    geojson = response.json()

    if geojson.get("type") != "FeatureCollection":
        raise ValueError("GeoJSON 형식이 FeatureCollection이 아닙니다.")

    # 모든 경계 코드와 속성 코드를 문자열로 정리
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        code = props.get("코드")

        if code is not None:
            code = str(code).strip()

            # 숫자형으로 저장되어 5자리보다 짧은 경우 앞을 0으로 채움
            if code.isdigit():
                code = code.zfill(5)

            props["코드"] = code

    return geojson


# -----------------------------
# 데이터 처리 및 지도 표시
# -----------------------------
try:
    population = load_population()
    geojson = load_geojson()

    # GeoJSON의 코드와 인구 데이터의 시군구 코드로 매칭
    geo_features = []

    for feature in geojson["features"]:
        props = feature.get("properties", {})
        code = props.get("코드")

        if code is None:
            continue

        code = str(code).strip()

        if code.isdigit():
            code = code.zfill(5)

        props["코드"] = code
        geo_features.append(feature)

    geojson["features"] = geo_features

    # 지도에 실제로 존재하는 코드와 인구 데이터가 있는 코드만 사용
    geo_codes = {
        feature["properties"]["코드"]
        for feature in geojson["features"]
    }

    population["시군구코드"] = population["시군구코드"].astype(str)

    matched = population[
        population["시군구코드"].isin(geo_codes)
    ].copy()

    if matched.empty:
        st.error(
            "인구 데이터와 GeoJSON의 시군구 코드가 일치하지 않습니다. "
            "두 파일의 코드 형식과 자료 범위를 확인해주세요."
        )
        st.stop()

    # GeoJSON 속성의 시군구 이름을 툴팁에 사용
    name_by_code = {
        feature["properties"]["코드"]:
        feature["properties"].get("시군구", "이름 없음")
        for feature in geojson["features"]
    }

    matched["시군구명"] = matched["시군구코드"].map(name_by_code)

    # 전국 지도는 GeoJSON에 포함된 경계를 유지하고
    # 데이터가 없는 지역은 별도로 색칠하지 않음
    st.subheader("전국 시군구별 고령화율")

    st.caption(
        "고령화율 = 65세 이상 인구 ÷ 전체 인구 × 100. "
        "색이 진할수록 고령화율이 높습니다."
    )

    fig = px.choropleth(
        matched,
        geojson=geojson,
        locations="시군구코드",
        featureidkey="properties.코드",
        color="고령화율",
        color_continuous_scale=[
            [0.0, "#fff7bc"],
            [0.25, "#fec44f"],
            [0.5, "#fe9929"],
            [0.75, "#ec7014"],
            [1.0, "#993404"]
        ],
        custom_data=["시군구명", "고령화율"],
        labels={"고령화율": "고령화율(%)"}
    )

    fig.update_traces(
        marker_line_color="white",
        marker_line_width=0.45,
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "고령화율: %{customdata[1]:.2f}%"
            "<extra></extra>"
        )
    )

    # 배경 타일 없이 경계만 표시
    fig.update_geos(
        visible=False,
        fitbounds="locations",
        showcountries=False,
        showcoastlines=False,
        showland=False,
        showlakes=False,
        showrivers=False,
        bgcolor="rgba(0,0,0,0)"
    )

    fig.update_layout(
        height=800,
        margin={"r": 0, "t": 20, "l": 0, "b": 0},
        paper_bgcolor="rgba(0,0,0,0)",
        coloraxis_colorbar={
            "title": "고령화율(%)",
            "ticksuffix": "%"
        }
    )

    st.plotly_chart(fig, use_container_width=True)

    # -----------------------------
    # 요약 정보
    # -----------------------------
    st.subheader("📊 전국 시군구 통계")

    highest = matched.loc[matched["고령화율"].idxmax()]
    lowest = matched.loc[matched["고령화율"].idxmin()]
    average_rate = (
        matched["고령인구"].sum()
        / matched["전체인구"].sum()
        * 100
    )

    col1, col2, col3 = st.columns(3)

    col1.metric("지도에 표시된 시군구", f"{len(matched):,}개")
    col2.metric("전국 합산 고령화율", f"{average_rate:.2f}%")
    col3.metric("고령화율 최고 지역", str(highest["시군구명"]))

    st.write(
        f"**고령화율 최고:** {highest['시군구명']} "
        f"({highest['고령화율']:.2f}%)"
    )
    st.write(
        f"**고령화율 최저:** {lowest['시군구명']} "
        f"({lowest['고령화율']:.2f}%)"
    )

    # -----------------------------
    # 데이터 표
    # -----------------------------
    with st.expander("시군구별 고령화율 데이터 보기"):
        table = matched[
            ["시군구코드", "시군구명", "고령인구", "전체인구", "고령화율"]
        ].copy()

        table = table.rename(
            columns={
                "시군구코드": "코드",
                "시군구명": "시군구",
                "고령인구": "65세 이상 인구",
                "전체인구": "전체 인구",
                "고령화율": "고령화율(%)"
            }
        )

        table = table.sort_values("고령화율(%)", ascending=False)

        st.dataframe(
            table.style.format({
                "65세 이상 인구": "{:,.0f}",
                "전체 인구": "{:,.0f}",
                "고령화율(%)": "{:.2f}"
            }),
            use_container_width=True,
            hide_index=True
        )

except requests.RequestException as e:
    st.error(f"데이터를 내려받지 못했습니다: {e}")

except Exception as e:
    st.error(f"앱 실행 중 오류가 발생했습니다: {e}")
    st.info(
        "데이터의 열 이름과 GeoJSON의 코드 형식을 확인해주세요. "
        "문제가 계속되면 오류 메시지를 확인하세요."
    )
