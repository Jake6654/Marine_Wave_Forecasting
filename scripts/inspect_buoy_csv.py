from pathlib import Path

import pandas as pd

# parents[0] → scripts ,  parents[1] → data-project
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "data" / "raw" / "buoy_sample.csv"

# 관측지점 식별자와 시각 문자열을 보존하면서 원본 열을 확인한다.

df = pd.read_csv(
  CSV_PATH,
  encoding="cp949",
  encoding_errors="strict",
  dtype={
    "지점" : "string",
    "일시" : "string",
  },
  keep_default_na=False, # pandas의 기본 결측 문자열 목록을 자동 적용하지 않음
  na_values=[""],
)

# print("root: ", PROJECT_ROOT)
# print("CSV path: ", CSV_PATH)

# print(df.shape) # 행, 열

# print("\nColumn names: ")
# print(df.columns.tolist()) # 열을 파이썬 리스트로 반환

# print("\nFirst 5 rows:")
# print(df.head(5).to_string(index=False)) # 결과를 출력용 문자열로 바꾼다

# print("\nLast 3 rows:")
# print(df.tail(3).to_string(index=False))

# print(df.dtypes)

# df.info()

# print(df.isna().sum())

print(df[df["유의파고(m)"] >= 1.0].to_string(index=False))
