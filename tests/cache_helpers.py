import pandera.pandas as pa

SCHEMA = pa.DataFrameSchema(
    {"value": pa.Column(int, description="Value", metadata={"unit": None})}, strict=True
)
