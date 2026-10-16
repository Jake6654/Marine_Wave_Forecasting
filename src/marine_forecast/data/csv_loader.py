from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = frozenset(
  {
    "지점",
    "일시",
    "유의파고(m)",
  }
)

def read_buoy_csv(
    path: str | Path,
    *,
    encoding: str = "cp949"
) -> pd.DataFrame:
    """Read source text and validate the minimum CSV structure."""
    file_path = Path(path)

    # Preserve source strings before applying numeric and time rules.
    frame = pd.read_csv(
        file_path,
        encoding=encoding,
        encoding_errors="strict",
        dtype="string",
        keep_default_na=False,
        skip_blank_lines=False,
        on_bad_lines="error",
    )

    # Report missing required columns before downstrasm code access them
    missing_columns = REQUIRED_COLUMNS.difference(frame.columns)
    if missing_columns:
        raise ValueError(
            f"{file_path}: missing required columns: "
            f"{sorted(missing_columns)}"
        )


    # A header-only file cannot provide observation records
    if frame.empty:
        raise ValueError(
            f"{file_path}: CSV contains no observation records"
        )

    return frame