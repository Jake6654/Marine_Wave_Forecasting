from pathlib import Path

import pandas as pd

# parents[0] → scripts ,  parents[1] → data-project
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = PROJECT_ROOT / "data" / "raw" / "buoy_sample.csv"

# Preserver identifiers and timestamp text while inspecting the raw columns

df = pd.read_csv(
  CSV_PATH,
  encoding="cp949",
  encoding_errors="strict",
  dtype={
    "지점" : "string",
    "일시" : "string",
  },
  keep_default_na=False,
  na_values=[""],
)

print("root: ", PROJECT_ROOT)
print("CSV path: ", CSV_PATH)

print(df.shape)

print("\nColumn names: ")
print(df.columns.tolist())

print("\nFirst 5 rows:")
print(df.head(5).to_string(index=False))

print("\nLast 3 rows:")
print(df.tail(3).to_string(index=False))

print(df.dtypes)

df.info()

print(df.isna().sum())

print(df["유의파고(m)"])
print(df[["유의파고(m)"]])
