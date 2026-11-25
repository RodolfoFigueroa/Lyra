# Ledger 05: Style, Vocabulary, Prose, and Formatting Standards

House style rules and exact per-file style/formatting defects found across `docs/`.

---

## 1. Vocabulary & Jargon Standard

Apply consistently across all authored docs:

| Avoid / fix | Use instead | Notes |
| --- | --- | --- |
| `retained` (used 60+× for both SQLite & Redis) | **saved** (for completed results & reports); **active / live job** (in consumer docs); **RQ queue record** (in `operate/` only) | After `20c90a3b`, local users and server consumers never need to know about Redis or queue expiry—remove `"retained"` and `"after queue expiry"` caveats outside `operate/` and `concepts/lifecycle.md`. |
| `native` (overloaded 4 ways) | Name the thing literally: **DataFrame / GeoDataFrame**, **target provider records**, **original CRS**, **Python `Intervention`** | Only use "native" if explicitly contrasting with serialized JSON/Arrow. |
| `effective` (unexplained shorthand) | **resolved parameters (including defaults)** / **dataset after interventions** | Define once in `concepts/` and `reference/parameters.md`. |
| `publication` / `publish` (in local/user guides) | **save** / **commit to the result store** | Reserve "publish" for publishing packages/catalogs. |
| `live definitions` | **Python definitions** / **registered plugin** | Remnant contrasting with removed static manifests. |
| `ordinary` (13× filler in `plugins/`) | Drop the word or name the type (*parameters*, *DataFrames*, *tables*) | Repetitive AI verbal tic. |
| `adapter` / `handler` / `callback` / `function` (mixed) | **metric function** (or **handler**) for `@metric`; **callback** for operation selectors/mappers | Pick one and stay consistent across tutorial & guides. |
| `selector` (for `@location_operation`) | **location operation** | Reserve **target selector** for Ibis `target_selector`. |
| `interventions`, `correspondence`, `resolved use` | Define on first use with a 1-line example | E.g., *"interventions: temporary record additions or removals for one run."* |
| `agent Bearer credential` / `agent credential` / `agent key` | **agent key** (mention `Authorization: Bearer` once in auth section) | Unify across `quickstart`, `use/*`, `operate/*`. |
| `REST guide` vs `Use the HTTP API` | **HTTP API** | Matches `use/rest-api.md` title and `reference/generated/http.md`. |
| `client.result_tables()` vs `client.results.tables()`; `store.describe()` vs `store.describe_result()` | Pick one primary spelling in guides (`client.results.tables()`, `store.describe()`) and note aliases once in reference | Eliminates reader confusion over whether methods differ. |

---

## 2. Eliminating Spec-Sheet & AI-Prose Tells

- `[ ] STYLE-01` **Cut defensive negative-invariant chains** from How-to/Tutorial/Conceptual pages (e.g., `plugins/authoring.md:130-153`, `plugins/outputs.md:169-205`, `plugins/location-providers.md:115-133,189-193`, `concepts/provenance.md:32,67,97,132,134,150`, `concepts/caching.md:108-112`, `reference/location-artifacts.md:80-86`, `use/location-exports.md:162,179`). State positive guarantees once in `concepts/` or `reference/`.
- `[ ] STYLE-02` **Remove `"X does not / cannot prove Y"` template disclaimers** (`plugins/authoring.md:153`, `outputs.md:199,232`, `descriptions.md:44,57`, `testing.md:131`, `location-providers.md:127`, `canonical-selection.md:46`, `tutorial/testing.md:66`).
- `[ ] STYLE-03` **Remove agent-skill prompt rubric prose** leaked into human docs (`plugins/descriptions.md:36,42,44` echoing `skills/lyra-metric/SKILL.md:58-63`).
- `[ ] STYLE-04` **Rewrite identical template openers across all 8 tutorial chapters** (`"Start with ...; you'll..."` at `first-metric.mdx:12`, `select-streets.mdx:12`, `parameters.mdx:12`, `dependencies.mdx:13`, `blocks.mdx:13`, `canonical.md:9`, `testing.md:9`, `package.md:10`).
- `[ ] STYLE-05` **Smooth cold-open & stop-start paragraph stacks** (e.g., `operate/admin-cli.md:22-28`, `operate/deployment.mdx:53,79`, `operate/runbook.md:59`, `use/local-execution.md:69-81`, `use/mcp.md:80-92`, `use/python-client.md:242-252`, `use/results.md:217-223,294,389-395`).
- `[ ] STYLE-06` **Fix remaining weasel words & passive/personified spots:**
  - `operate/runbook.md:15` (`Typical use`), `L81` (`within approximately two minutes`)
  - `use/jobs.md:40` (`roughly every five seconds`)
  - `operate/caching.md:35` (`Closed alpha doesn’t promise migrations`)
  - `use/results.md:324` (`storage and retrieval promise no implicit row order`)
  - `use/python-client.md:313`, `use/jobs.md:52`, `operate/configuration.md:86`, `operate/caching.md:57-62` (passive voice)

---

## 3. Spelling, Typography, and Frontmatter Consistency

### `[ ] STYLE-07` Standardize on US English (fix all UK spellings)
Current corpus has 90+ US spellings (`catalog`, `normalize`, `serialize`, `behavior`, `neighborhood`, `meter`) mixed with these UK leftovers:
- `docs/src/content/docs/start/local.mdx:11` (`metres`)
- `docs/src/content/docs/plugins/tutorial/first-metric.mdx:18,22,26,30` (`centreline`, `metre`, `metres`)
- `docs/src/content/docs/plugins/tutorial/parameters.mdx:58,61` (`centreline`, `metres`)
- `docs/src/content/docs/plugins/earth-engine.md:62` (`metres`)
- `docs/src/content/docs/plugins/location-operations.md:153` (`ten-metre`)
- `docs/src/content/docs/reference/units.md:13,17-19` (`kilometres`, `metres`, `kilometre` — right beside Pint's `"kilometer"` / `"meter"`)
- `docs/src/content/docs/reference/target-selection.md:16,72` (`metres`, `metre`)
- `docs/src/content/docs/concepts/lifecycle.md:56` (`cancelling` → `canceling`)

### `[ ] STYLE-08` Standardize sidebar labels & `contentType`
- Fix mechanical Python `str.title()` capitalization of articles/prepositions/conjunctions (`A`, `An`, `And`, `For`, `In`, `The`, `To`, `With`) across `docs/navigation.json` (`"Build A City Plugin"`, `"Develop And Deliver"` vs `"Deploy and Operate"`), `docs/scripts/generate_docs.py:189`, and 34 page frontmatter `sidebar.label` entries. Pick standard Title Case (lowercase minor words) or sentence case matching H1s.
- Add or standardize missing `sidebar.label` on `use/location-exports.md`, `reference/locations.md`, `reference/location-artifacts.md`.
- Align tutorial chapter titles between `plugins/tutorial/index.md:54-61`, page `title`s, and `sidebar.label`s.
- Fix wrong `contentType` values:
  - `plugins/location-providers.md:4`: `Reference` → `How-to`
  - `plugins/location-operations.md:4`: `Tutorial` → `How-to`

### `[ ] STYLE-09` Fix hard-wrapped paragraphs & straight apostrophes
Every other file uses unwrapped single-line paragraphs and curly apostrophes (`’`). Fix these 3 files (also removing verbatim copy-paste):
- `docs/src/content/docs/concepts/graphs.md:70-76` (`invocation's`)
- `docs/src/content/docs/concepts/caching.md:114-120` (`invocation's`)
- `docs/src/content/docs/operate/caching.md:37-43,57-62,64-70` (`invocation's`, `provider's`)

### `[ ] STYLE-10` Standardize units, statuses, acronyms, punctuation, & links
- **Byte limits:** Unify `65,536 bytes` (`use/mcp.md:92,115,139,169`), `65,536 encoded bytes` (`use/results.md:132`), and `64 KiB` (`use/results.md:37`) → pick `64 KiB` (or `64 KB`) everywhere.
- **Durations & non-breaking spaces:** Unify `5s`/`30s`/`1s` vs `five seconds`/`two seconds`; remove the lone `&nbsp;` in `start/local.mdx:48` (`100&nbsp;m`) and Unicode NBSP in `reference/mcp-payloads.md:36` (`150\u00a0m`).
- **HTTP status formatting:** Consistently backtick or consistently leave plain (don't mix `` HTTP `404` `` and `HTTP 404` inside `use/rest-api.md` and `use/results.md`).
- **Hyphenation:** Decide between closed (`nonblank`, `nonempty`, `nonnegative`, `noncanonical`) and hyphenated (`non-null`) consistently.
- **Contractions:** Use contractions consistently (`don’t`, `doesn’t`, `you’ll`) instead of alternating contracted and formal forms across sibling guides.
- **Acronym first use:** Spell out `MCP`, `CLI`, `CIDR`, `WGS84`, `TOML` on first use per page where not in the allowed-abbreviations list (`use/results.md:37`, `use/rest-api.md:130`, `use/mcp.md:102`, `operate/configuration.md:24,92`, `operate/caching.md:68`, `operate/plugins.mdx:52`).
- **List/code lead-ins & punctuation:** Introduce lists with colons (`operate/runbook.md:45`, `operate/updates.md:71`, `use/rest-api.md:67`, `operate/admin-cli.md:47`, `plugins/tutorial/index.md:24`); match trailing periods on full-sentence list items (`operate/caching.md:49-51`, `operate/updates.md:73-76`, `use/rest-api.md:56-61`, `use/mcp.md:27-32`).
- **Extra blank line / empty structure:** Remove extra blank line at `use/results.md:298`; add H2 headings to `plugins/earth-engine.md`; fix mixed numbered/unnumbered H2s in `use/mcp.md:15-218`.
- **GitHub repo casing:** Use `https://github.com/RodolfoFigueroa/Lyra/...` everywhere (fix lowercase `/lyra/` in `operate/caching.md:69`, `reference/index.md:47`, `plugins/canonical-selection.md:48`, `plugins/location-providers.md:133,139`).

---

## 4. Code Blocks, `.md`/`.mdx`, and Highlight Ranges

### `[ ] STYLE-11` Standardize `.md` vs `.mdx` and `<Steps>`
- Convert JSX-free `.mdx` files to `.md`: `plugins/dependencies.mdx`, `plugins/quickstart.mdx` (if not deleted).
- Standardize `plugins/tutorial/*` so all chapters use `.mdx` + `<Steps>` + `<CodeExample>` (currently Ch. 1–5 do, Ch. 6–8 abruptly drop `<Steps>` and `<CodeExample>`).
- Decide whether multi-step `operate/` procedures (`updates.md`, `troubleshooting.md`) should use `<Steps>` like `deployment.mdx` and `plugins.mdx`.

### `[ ] STYLE-12` Stop splitting decorators from `def` across two fenced blocks
Rejoin `@metric(...)` / `@location_operation(...)` and their function bodies (drop *"Append the handler immediately after its decorator"*) in:
- `docs/src/content/docs/plugins/tutorial/blocks.mdx:50-70,88-119`
- `docs/src/content/docs/plugins/tutorial/canonical.md:15-36`
- `docs/src/content/docs/plugins/location-example.mdx:170-244` (if not deleted)
- `docs/src/content/docs/plugins/location-operations.md:72-106`

### `[ ] STYLE-13` Fix all broken, out-of-bounds, or meaningless line-highlight ranges
- `docs/src/content/docs/plugins/resources.md:158`: `{6,9-11}` on a **9-line** block (out of bounds!)
- `docs/src/content/docs/plugins/outputs.md:99`: `{5,6}` highlights `nullable`+`description` instead of `"Int64"`+`nullable` (`{4,5}`)
- `docs/src/content/docs/plugins/authoring.md:21`: `{6-10}` misses closing `)` on line 11
- `docs/src/content/docs/plugins/location-operations.md:116`: `{10-16}` cuts off assertion lines 17–20
- `docs/src/content/docs/plugins/dependencies.mdx:116`: `{1-3}` highlights entire 3-line block
- `docs/src/content/docs/plugins/testing.md:102`: `{2-6}` highlights entire function body
- `docs/src/content/docs/use/location-exports.md:28`: `{8-11}` cuts off lines 12–13 → `{8-13}`
- `docs/src/content/docs/use/local-execution.md:166`: `{5-7}` misses closing `},` on line 8 → `{6-8}`
- `docs/src/content/docs/use/rest-api.md:75` & `use/mcp.md:44`: `{3-10}` includes closing root `}` → `{3-9}`
- Tutorial `<CodeExample>` embeds:
  - `plugins/tutorial/first-metric.mdx:20` (`calculations.py#lengths`, `{7-9}` misses line 10)
  - `plugins/tutorial/parameters.mdx:35` & `dependencies.mdx:44` (`metrics.py#parameters`, `{4-8}` misses line 9)
  - `plugins/tutorial/parameters.mdx:51` (`checkpoints.py#direct`, `{8,16-20}` cuts off args & lines 21–23)
  - `plugins/tutorial/blocks.mdx:42` (`metrics.py#membership`, `{6-7,15-16}` cuts off lines 17–18)
  - `plugins/tutorial/blocks.mdx:80` (`metrics.py#bindings`, `{1-3,9}` misses `.bind(...)` on line 10)
  - `plugins/tutorial/blocks.mdx:129` (`{4-7}` highlights `],` and misses `CityBlockProvider` on lines 8–11)

### `[ ] STYLE-14` Fix CLI command prefix & code notation inconsistencies
- Standardize `lyra-admin` invocation prefix across `operate/` (currently switches unpredictably between `uv run --project ../.. lyra-admin`, `docker compose exec api lyra-admin`, `uv run lyra-admin`, and bare `lyra-admin`).
- Standardize `LocationDependency(operation="...")` keyword style (fix positional `LocationDependency("city_streets_for_blocks")` in `examples/city-plugin/city_example/metrics.py` / `blocks.mdx:80`).
- Standardize `DataFrame` / `GeoDataFrame` backticking and `plugin.describe(...)` instance notation.
- Wrap lines >80 columns in `plugins/tutorial/dependencies.mdx:148-149` and `examples/city-plugin/` `# docs:start` regions.
