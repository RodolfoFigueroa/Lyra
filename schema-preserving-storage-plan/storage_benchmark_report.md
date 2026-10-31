# Storage benchmark results

These measurements describe the tested implementations. No storage format is selected or recommended by this report.

## Scope and method

- Six paths: current SDK `TableCache`; native Arrow IPC uncompressed, LZ4, and Zstandard; native Parquet Snappy and Zstandard. Compression levels and threading use library defaults.
- Four deterministic fixtures, at 100 and 10,000 rows, plus 100,000 rows for numeric/mixed/points and 20,000 for polygons. Numeric: 16 random float64 columns. Mixed: eight columns containing IDs, nullable integers/booleans/strings, repeated labels, ordered categories with an unused label, nanosecond UTC timestamps, Arrow date32, and floats. Spatial fixtures add points or 129-coordinate buffered polygons to the mixed columns.
- 72 timed cases, five repetitions per phase (1,440 timings). Each case runs in a fresh spawned process; cases execute sequentially. Fixture generation, imports, initial schema validation and correctness comparisons are outside timings. Fixed case order; this is one machine and one campaign, not a statistical claim about every workload.
- Final run used temporary files on `/dev/nvme0n1p2`, ext4, under the repository. The initial RAM-backed `/tmp` pilot was excluded. Hardware: AMD Ryzen 9 9950X, 32 logical CPUs; Arrow reported 32 CPU threads.
- Write includes pandas/GeoPandas conversion and buffered file publication, but does not measure durable fsync latency. The current cache also includes its locks, manifest, atomic publication, and category sidecars. Native candidates write one file without that bundle machinery, so current-versus-candidate differences include wrapper overhead.
- Warm read includes file read and reconstruction into pandas/GeoPandas. Validated read additionally runs a strict, ordered Pandera schema with the fixture dtypes, nullable columns and a simple row-count check. This does not simulate arbitrary expensive plugin checks or the SDK's complete execution path.
- Advised read follows fsync and `POSIX_FADV_DONTNEED` on artifact files, outside the timer. This requests OS page-cache eviction; it does not prove physical-device coldness or evict hardware caches. It excludes schema validation.
- Memory is sampled resident-set growth at 1 ms intervals and at operation completion, relative to RSS immediately before each operation. The first write and first validated read are measured before timing repetitions, after fixture construction and initial schema validation. Read follows write in the same case process, so allocator reuse remains possible. Short peaks may be missed; values are observations, not exact allocation accounting.
- Spatial candidates use GeoPandas' native GeoArrow WKB conversion. The current metric cache uses GeoParquet. This does not benchmark a redesign of the existing location geometry buffers, location snapshot ownership or content fingerprinting.
- No external services, executor concurrency changes, package installations or production changes. The full test suite retains its service-access guard.

Environment: pandas 3.0.5, pyarrow 26.0.0, pandera 0.35.0, geopandas 1.1.4; Python 3.11.15.

## Fidelity

The 72-case matrix covers populated, zero-row and all-null property columns for all four fixtures. All spatial cases retain non-null geometry. Comparisons check exact scalar values/dtypes, category vocabulary/order, row/column order, CRS, exact WKB and floating sign bits, then run the fixture Pandera schema. Six additional pytest cases cover signed/unsigned 64-bit boundaries, nullable floats and signed zero.

| Path | Passed / 12 | Observed failures |
| --- | ---: | --- |
| current | 12/12 | None |
| ipc-none | 10/12 | Empty points, polygons: categorical vocabulary lost. |
| ipc-lz4 | 10/12 | Empty points, polygons: categorical vocabulary lost. |
| ipc-zstd | 10/12 | Empty points, polygons: categorical vocabulary lost. |
| parquet-snappy | 9/12 | Empty mixed, points, polygons: categorical vocabulary lost. |
| parquet-zstd | 9/12 | Empty mixed, points, polygons: categorical vocabulary lost. |

All populated and all-null cases passed. The current cache preserves empty categories using its existing category sidecars. IPC explicitly writes an empty RecordBatch to retain dictionaries; even then, the tested GeoPandas Arrow path loses the empty categorical vocabulary. Native Parquet loses it for both ordinary and spatial empty frames. These are failures of the tested native round-trip paths, not proof that every possible implementation of either format must fail. No repair layer was added. Timings below use populated fixtures, which passed for every path.

## Largest fixtures

Times are medians in milliseconds; sizes and memory are MiB. Raw repetition values are retained in `storage_benchmark_results.json`.

### numeric: 100,000 rows

| Path | Write ms | Warm read ms | Validated read ms | Advised read ms | File MiB | Write RSS +MiB | Read+validate RSS +MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| current | 40.35 | 5.07 | 7.99 | 11.76 | 15.32 | 30.03 | 114.08 |
| ipc-none | 3.08 | 1.00 | 2.45 | 6.64 | 12.21 | 3.45 | 35.13 |
| ipc-lz4 | 3.40 | 1.52 | 3.15 | 7.29 | 12.21 | 16.48 | 26.77 |
| ipc-zstd | 3.81 | 1.88 | 3.56 | 8.11 | 11.71 | 20.30 | 39.50 |
| parquet-snappy | 38.63 | 3.01 | 4.84 | 10.27 | 15.32 | 29.90 | 111.27 |
| parquet-zstd | 47.73 | 3.57 | 4.91 | 10.00 | 14.69 | 30.28 | 108.77 |

### mixed: 100,000 rows

| Path | Write ms | Warm read ms | Validated read ms | Advised read ms | File MiB | Write RSS +MiB | Read+validate RSS +MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| current | 15.98 | 4.21 | 6.20 | 6.09 | 2.66 | 33.98 | 44.86 |
| ipc-none | 1.96 | 0.93 | 2.75 | 4.12 | 6.37 | 3.47 | 10.57 |
| ipc-lz4 | 2.50 | 1.45 | 3.29 | 3.56 | 2.72 | 24.09 | 14.87 |
| ipc-zstd | 2.69 | 1.56 | 3.27 | 2.67 | 1.14 | 27.17 | 18.75 |
| parquet-snappy | 14.00 | 3.46 | 5.10 | 4.81 | 2.65 | 31.98 | 46.49 |
| parquet-zstd | 15.81 | 3.18 | 4.82 | 4.12 | 1.60 | 32.31 | 53.04 |

### points: 100,000 rows

| Path | Write ms | Warm read ms | Validated read ms | Advised read ms | File MiB | Write RSS +MiB | Read+validate RSS +MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| current | 38.74 | 57.31 | 60.57 | 60.06 | 4.41 | 48.66 | 92.45 |
| ipc-none | 17.23 | 41.03 | 43.65 | 45.59 | 8.76 | 9.94 | 39.88 |
| ipc-lz4 | 19.48 | 42.06 | 44.98 | 44.56 | 4.79 | 38.70 | 38.18 |
| ipc-zstd | 21.13 | 45.34 | 48.83 | 49.69 | 2.90 | 37.98 | 38.27 |
| parquet-snappy | 33.23 | 47.29 | 50.70 | 51.57 | 4.40 | 48.43 | 98.21 |
| parquet-zstd | 36.49 | 50.27 | 52.04 | 50.79 | 3.14 | 40.78 | 100.73 |

### polygons: 20,000 rows

| Path | Write ms | Warm read ms | Validated read ms | Advised read ms | File MiB | Write RSS +MiB | Read+validate RSS +MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| current | 81.82 | 85.79 | 64.93 | 74.67 | 32.89 | 159.60 | 208.19 |
| ipc-none | 60.46 | 34.68 | 36.51 | 47.96 | 40.97 | 150.79 | 71.15 |
| ipc-lz4 | 81.76 | 36.15 | 37.87 | 44.78 | 25.48 | 157.44 | 75.52 |
| ipc-zstd | 108.40 | 51.47 | 53.28 | 58.83 | 21.87 | 159.97 | 101.41 |
| parquet-snappy | 78.46 | 49.74 | 52.64 | 62.21 | 32.88 | 159.39 | 207.36 |
| parquet-zstd | 113.51 | 57.47 | 59.29 | 65.19 | 21.98 | 159.68 | 165.88 |

## All timing repetitions summarized

Each timing cell is median [minimum–maximum], milliseconds. Differences near these ranges should not be treated as established rankings.

| Fixture | Rows | Path | Write | Warm read | Validated read | Advised read |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| numeric | 100 | current | 1.64 [1.43–1.73] | 1.82 [1.78–1.86] | 2.74 [2.66–2.88] | 2.18 [2.00–2.49] |
| numeric | 100 | ipc-none | 0.47 [0.41–0.55] | 0.20 [0.18–0.20] | 0.99 [0.97–1.01] | 0.39 [0.36–0.51] |
| numeric | 100 | ipc-lz4 | 0.57 [0.53–0.67] | 0.27 [0.25–0.28] | 1.14 [1.09–1.82] | 0.52 [0.48–0.72] |
| numeric | 100 | ipc-zstd | 0.65 [0.54–0.68] | 0.27 [0.26–0.32] | 1.12 [1.07–1.21] | 0.46 [0.44–0.61] |
| numeric | 100 | parquet-snappy | 0.76 [0.68–0.79] | 0.69 [0.63–0.77] | 1.48 [1.43–1.50] | 0.79 [0.73–0.92] |
| numeric | 100 | parquet-zstd | 0.83 [0.82–0.89] | 0.79 [0.64–0.85] | 1.56 [1.50–1.63] | 0.88 [0.79–0.95] |
| numeric | 10000 | current | 7.48 [7.11–8.42] | 2.15 [2.07–2.42] | 3.61 [3.40–3.87] | 3.15 [2.92–3.52] |
| numeric | 10000 | ipc-none | 1.58 [1.26–2.04] | 0.31 [0.29–0.34] | 1.20 [1.14–1.39] | 1.08 [1.05–1.42] |
| numeric | 10000 | ipc-lz4 | 1.56 [1.43–1.98] | 0.35 [0.32–0.40] | 1.21 [1.20–1.26] | 1.15 [1.15–1.22] |
| numeric | 10000 | ipc-zstd | 1.75 [1.72–2.26] | 0.40 [0.38–0.50] | 1.37 [1.26–1.61] | 1.26 [1.23–1.32] |
| numeric | 10000 | parquet-snappy | 6.13 [5.95–6.57] | 0.75 [0.72–0.90] | 1.73 [1.60–1.81] | 1.87 [1.80–2.19] |
| numeric | 10000 | parquet-zstd | 7.07 [6.80–7.62] | 0.78 [0.74–1.04] | 1.73 [1.64–1.77] | 1.91 [1.84–1.99] |
| numeric | 100000 | current | 40.35 [39.51–41.11] | 5.07 [4.50–5.32] | 7.99 [7.32–8.19] | 11.76 [11.20–12.14] |
| numeric | 100000 | ipc-none | 3.08 [2.98–3.32] | 1.00 [0.91–1.19] | 2.45 [2.32–2.76] | 6.64 [6.46–7.27] |
| numeric | 100000 | ipc-lz4 | 3.40 [3.36–3.94] | 1.52 [1.32–1.55] | 3.15 [2.85–3.23] | 7.29 [6.78–8.19] |
| numeric | 100000 | ipc-zstd | 3.81 [3.75–4.72] | 1.88 [1.68–2.03] | 3.56 [3.41–3.69] | 8.11 [7.41–8.31] |
| numeric | 100000 | parquet-snappy | 38.63 [38.03–39.19] | 3.01 [2.92–3.45] | 4.84 [4.56–5.46] | 10.27 [9.50–11.31] |
| numeric | 100000 | parquet-zstd | 47.73 [46.29–48.21] | 3.57 [3.18–3.91] | 4.91 [4.87–5.88] | 10.00 [9.66–10.68] |
| mixed | 100 | current | 1.72 [1.65–1.90] | 1.87 [1.71–2.16] | 3.30 [3.21–3.46] | 2.15 [1.92–2.25] |
| mixed | 100 | ipc-none | 0.42 [0.40–0.48] | 0.42 [0.40–0.51] | 1.81 [1.77–1.96] | 0.59 [0.58–0.63] |
| mixed | 100 | ipc-lz4 | 0.49 [0.46–0.90] | 0.46 [0.45–0.51] | 1.90 [1.80–2.05] | 0.64 [0.62–0.68] |
| mixed | 100 | ipc-zstd | 0.49 [0.46–1.03] | 0.44 [0.43–0.56] | 1.83 [1.77–2.04] | 0.60 [0.58–0.72] |
| mixed | 100 | parquet-snappy | 0.62 [0.57–0.72] | 0.81 [0.75–1.27] | 2.23 [2.11–2.69] | 0.96 [0.81–1.23] |
| mixed | 100 | parquet-zstd | 0.77 [0.66–0.81] | 0.87 [0.83–0.98] | 2.36 [2.14–2.44] | 1.02 [0.95–1.40] |
| mixed | 10000 | current | 4.48 [3.99–4.92] | 2.10 [1.98–2.24] | 3.60 [3.56–3.96] | 2.55 [2.39–3.28] |
| mixed | 10000 | ipc-none | 1.12 [0.89–1.53] | 0.55 [0.45–0.60] | 1.98 [1.86–2.15] | 1.23 [1.22–1.43] |
| mixed | 10000 | ipc-lz4 | 1.23 [0.97–2.00] | 0.63 [0.51–0.68] | 2.19 [1.86–2.33] | 1.13 [1.06–1.23] |
| mixed | 10000 | ipc-zstd | 1.13 [1.04–1.73] | 0.64 [0.58–0.75] | 2.02 [1.95–2.23] | 1.06 [1.06–1.16] |
| mixed | 10000 | parquet-snappy | 3.01 [2.70–3.56] | 1.00 [0.98–1.36] | 2.51 [2.48–3.15] | 1.41 [1.37–1.50] |
| mixed | 10000 | parquet-zstd | 3.19 [3.11–3.87] | 1.06 [0.97–1.32] | 2.62 [2.49–2.77] | 1.25 [1.21–1.48] |
| mixed | 100000 | current | 15.98 [15.86–16.20] | 4.21 [4.09–5.10] | 6.20 [5.70–7.26] | 6.09 [5.81–6.77] |
| mixed | 100000 | ipc-none | 1.96 [1.81–2.42] | 0.93 [0.87–1.15] | 2.75 [2.68–3.08] | 4.12 [4.09–4.53] |
| mixed | 100000 | ipc-lz4 | 2.50 [2.36–3.12] | 1.45 [1.40–1.69] | 3.29 [3.11–3.59] | 3.56 [3.35–4.34] |
| mixed | 100000 | ipc-zstd | 2.69 [2.33–2.94] | 1.56 [1.41–1.74] | 3.27 [3.10–4.46] | 2.67 [2.43–3.59] |
| mixed | 100000 | parquet-snappy | 14.00 [13.50–14.42] | 3.46 [2.96–4.31] | 5.10 [4.93–6.37] | 4.81 [4.45–5.52] |
| mixed | 100000 | parquet-zstd | 15.81 [15.68–16.36] | 3.18 [2.74–3.65] | 4.82 [4.68–5.11] | 4.12 [3.66–4.40] |
| points | 100 | current | 2.86 [2.66–2.99] | 12.83 [12.74–13.09] | 15.36 [15.13–15.75] | 13.04 [12.96–13.11] |
| points | 100 | ipc-none | 0.89 [0.85–0.98] | 0.87 [0.86–0.96] | 2.93 [2.92–3.68] | 1.18 [1.08–1.22] |
| points | 100 | ipc-lz4 | 1.06 [0.92–1.25] | 0.93 [0.90–1.05] | 3.08 [3.00–3.41] | 1.21 [1.11–1.89] |
| points | 100 | ipc-zstd | 0.97 [0.95–1.14] | 0.90 [0.90–1.02] | 2.97 [2.91–3.28] | 1.08 [1.05–1.20] |
| points | 100 | parquet-snappy | 1.16 [1.09–1.27] | 1.37 [1.31–1.49] | 3.44 [3.36–3.67] | 1.44 [1.38–1.63] |
| points | 100 | parquet-zstd | 1.16 [1.12–1.31] | 1.37 [1.26–1.52] | 3.57 [3.29–4.51] | 1.48 [1.34–2.24] |
| points | 10000 | current | 7.55 [7.40–7.88] | 14.47 [14.32–38.33] | 16.61 [16.28–17.14] | 14.74 [14.71–15.09] |
| points | 10000 | ipc-none | 3.27 [3.00–3.56] | 1.91 [1.89–24.04] | 4.35 [4.04–4.75] | 2.71 [2.61–2.89] |
| points | 10000 | ipc-lz4 | 3.54 [3.38–3.71] | 2.07 [2.03–25.45] | 4.54 [4.23–4.83] | 2.66 [2.57–2.82] |
| points | 10000 | ipc-zstd | 3.59 [3.28–3.93] | 2.18 [2.03–25.18] | 4.63 [4.25–5.13] | 2.89 [2.51–2.98] |
| points | 10000 | parquet-snappy | 5.77 [5.59–6.01] | 2.90 [2.54–26.08] | 5.30 [5.02–5.69] | 3.27 [3.07–3.43] |
| points | 10000 | parquet-zstd | 5.98 [5.85–6.49] | 2.98 [2.56–26.61] | 5.46 [4.98–5.74] | 3.30 [2.92–3.49] |
| points | 100000 | current | 38.74 [37.44–41.81] | 57.31 [56.05–59.90] | 60.57 [58.74–61.14] | 60.06 [57.90–84.92] |
| points | 100000 | ipc-none | 17.23 [16.74–18.70] | 41.03 [40.20–62.69] | 43.65 [42.96–45.83] | 45.59 [44.27–67.82] |
| points | 100000 | ipc-lz4 | 19.48 [18.60–19.86] | 42.06 [40.62–65.01] | 44.98 [44.03–45.55] | 44.56 [42.75–70.01] |
| points | 100000 | ipc-zstd | 21.13 [20.03–22.15] | 45.34 [43.25–76.13] | 48.83 [45.61–52.93] | 49.69 [43.95–87.41] |
| points | 100000 | parquet-snappy | 33.23 [32.54–34.32] | 47.29 [44.77–90.51] | 50.70 [47.72–59.86] | 51.57 [44.98–75.16] |
| points | 100000 | parquet-zstd | 36.49 [36.40–39.10] | 50.27 [45.93–85.05] | 52.04 [47.38–56.88] | 50.79 [48.25–86.24] |
| polygons | 100 | current | 3.57 [3.38–3.74] | 13.64 [13.33–13.79] | 15.99 [15.68–17.03] | 14.52 [14.09–15.05] |
| polygons | 100 | ipc-none | 1.17 [1.11–1.28] | 1.04 [1.00–1.08] | 3.22 [3.05–3.42] | 1.50 [1.46–1.55] |
| polygons | 100 | ipc-lz4 | 1.47 [1.32–1.61] | 1.12 [1.08–1.15] | 3.20 [3.14–3.95] | 1.60 [1.52–1.72] |
| polygons | 100 | ipc-zstd | 1.74 [1.46–1.91] | 1.22 [1.13–1.44] | 3.44 [3.16–3.83] | 1.66 [1.57–1.76] |
| polygons | 100 | parquet-snappy | 1.50 [1.45–1.68] | 1.44 [1.37–1.89] | 3.58 [3.54–3.97] | 1.64 [1.61–2.13] |
| polygons | 100 | parquet-zstd | 1.70 [1.65–1.87] | 1.72 [1.55–1.95] | 3.76 [3.55–3.90] | 1.89 [1.67–2.03] |
| polygons | 10000 | current | 43.40 [42.89–45.23] | 38.03 [37.85–60.06] | 40.91 [40.42–41.06] | 44.80 [43.94–45.08] |
| polygons | 10000 | ipc-none | 30.63 [30.10–31.52] | 16.18 [15.98–39.64] | 18.81 [18.56–19.23] | 24.35 [24.09–24.91] |
| polygons | 10000 | ipc-lz4 | 42.45 [41.73–44.57] | 18.44 [17.78–44.94] | 21.15 [21.06–21.63] | 24.67 [24.26–26.96] |
| polygons | 10000 | ipc-zstd | 55.24 [54.23–55.95] | 26.69 [25.31–50.71] | 29.10 [27.98–29.75] | 31.11 [30.22–31.84] |
| polygons | 10000 | parquet-snappy | 40.30 [39.97–41.04] | 26.62 [26.08–50.13] | 28.54 [28.27–29.39] | 33.27 [32.35–33.60] |
| polygons | 10000 | parquet-zstd | 59.08 [58.11–60.57] | 30.33 [29.52–53.81] | 33.40 [32.30–34.33] | 35.30 [33.98–36.25] |
| polygons | 20000 | current | 81.82 [81.58–83.79] | 85.79 [61.58–92.06] | 64.93 [64.48–68.18] | 74.67 [73.92–76.35] |
| polygons | 20000 | ipc-none | 60.46 [59.20–60.98] | 34.68 [32.98–59.11] | 36.51 [35.94–36.85] | 47.96 [47.29–48.74] |
| polygons | 20000 | ipc-lz4 | 81.76 [80.49–83.08] | 36.15 [35.14–58.31] | 37.87 [37.45–38.60] | 44.78 [44.21–47.45] |
| polygons | 20000 | ipc-zstd | 108.40 [107.74–111.73] | 51.47 [50.33–74.29] | 53.28 [52.62–54.13] | 58.83 [58.23–59.22] |
| polygons | 20000 | parquet-snappy | 78.46 [76.60–79.62] | 49.74 [48.93–73.66] | 52.64 [52.32–53.08] | 62.21 [61.02–66.28] |
| polygons | 20000 | parquet-zstd | 113.51 [113.40–114.65] | 57.47 [55.92–81.43] | 59.29 [58.60–59.83] | 65.19 [64.65–66.52] |

## Interpretation limits

- Numeric data are random floats, while mixed data contain repeated labels and monotonic integers/timestamps. Different entropy and cardinality can reverse compression-size comparisons. For example, default Parquet dictionary encoding is not necessarily beneficial for random unique floating values.
- Full artifact reads were measured. Predicate pushdown, column-only disk reads, memory mapping, larger-than-RAM workloads, remote storage, cold process startup and application-level cache misses were not measured. In-memory dependency reuse never touches these codecs.
- This does not establish end-to-end speedups for the proposed schema-preserving Locations implementation. Cache integrity hashing, immutable snapshot construction and arbitrary producer checks may change the balance.
- Native paths that failed the empty-category checks have unresolved fidelity work regardless of their populated-table timings. The report makes no choice between correcting those paths, retaining existing metadata handling, or choosing a different format.

## Reproduction and verification

```sh
uv run python tests/test_schema_storage_benchmark.py --output schema-preserving-storage-plan/storage_benchmark_results.json
uv run pytest tests/test_schema_storage_benchmark.py -q
```

Run the benchmark from the repository on a disk-backed filesystem, without competing tests or benchmarks. The harness creates and removes temporary directories in the current directory.

Verification completed:

- `uv run ruff format`: passed (305 files).
- `uv run ruff check`: passed.
- `uv run ty check`: passed.
- `uv run pytest tests/test_schema_storage_benchmark.py -q`: 30 passed.
- `uv run pytest --cov=lyra_app --cov=lyra --cov-report=term-missing --cov-report=xml`: 2,201 passed, two warnings, 95% coverage; `coverage.xml` generated. Run with the required sandbox escalation.

The new Python work is confined to `tests/test_schema_storage_benchmark.py`. Harness formatting, typing, and null-aware signed-zero comparisons were corrected during verification. No production files changed. No checks remain outstanding and no test-suite failures remain. The 12 experimental fidelity failures above remain reported observations, separate from the passing populated/boundary regression tests.
