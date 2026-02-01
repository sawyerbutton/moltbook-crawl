# Moltbook Community Observatory

Crawler, viewer, and analysis toolkit for [Moltbook](https://www.moltbook.com) — an AI Agent autonomous social platform where all users are AI Agents.

## Dataset

Data collected over 5 days (2026-01-27 to 2026-02-01):

| Data | Count |
|------|-------|
| Posts | 70,741 |
| Comments | 57,704 |
| Agents | 14,216 |
| Communities (Submolts) | 13,780 |

Stored in a single SQLite database (`moltbook.db`, ~147 MB, git-ignored).

## Project Structure

```
moltbook-crawl/
├── main.py                 # CLI entry point (crawl/sync/serve/daemon)
├── config.py               # Configuration dataclass
├── requirements.txt        # Python dependencies
│
├── crawler/                # Async data collection
│   ├── client.py           # HTTP client with token-bucket rate limiting & retries
│   ├── sync.py             # Full crawl & incremental sync orchestration
│   └── scheduler.py        # Periodic sync daemon
│
├── store/
│   └── db.py               # SQLite storage (WAL mode, schema, CRUD, queries)
│
├── viewer/
│   └── app.py              # Streamlit web UI (6 pages)
│
├── analysis/               # Stubs for future LLM-powered analysis
│   ├── agents.py
│   ├── topics.py
│   └── trends.py
│
├── REPORT.md               # Comprehensive data report (Chinese)
└── reports/
    ├── 01-community-culture.md   # Topic & culture deep dive
    ├── 02-agent-behavior.md      # Agent behavior pattern analysis
    └── 03-ecosystem-health.md    # Platform health assessment
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Set API key (optional, enables author attribution & comment fetching)
export MOLTBOOK_API_KEY="your-key"

# Full crawl (submolts → posts → comments)
python main.py crawl

# Incremental sync (new posts since last crawl)
python main.py sync

# Start the Streamlit viewer
python main.py serve

# Run as daemon (syncs every 15 minutes)
python main.py daemon
```

## Crawler

The crawler uses the Moltbook REST API (`/api/v1`) with:

- **Token-bucket rate limiting** — 100 token capacity, ~1.67 tokens/sec refill
- **Concurrency** — max 5 simultaneous requests
- **Retry logic** — 3 retries with exponential backoff for 5xx errors, `Retry-After` header support for 429s
- **Resume support** — sync state is persisted so interrupted crawls can resume

### Crawl pipeline

1. **`crawl_submolts`** — paginate through all communities
2. **`crawl_all_posts`** — paginate all posts sorted by newest
3. **`crawl_comments`** — fetch embedded comments via `GET /posts/{id}` (requires auth)

### Incremental sync

`python main.py sync` fetches posts newer than the last sync timestamp and attempts to fetch comments for posts that haven't been processed yet.

## Viewer

A Streamlit web app with 6 pages:

| Page | Description |
|------|-------------|
| Post Feed | Browse posts with sort/filter by community and agent |
| Agent Profiles | Search agents, view leaderboard rankings |
| Submolt Explorer | Browse communities, view top submolts and their posts |
| Conversation Explorer | View threaded comment trees for any post |
| Trends Dashboard | Daily activity charts, top posters & commenters |
| Crawl Status | Database stats, sync state, crawl operation logs |

## Database Schema

Core tables:

- **`posts`** — id, title, content, url, upvotes, downvotes, comment_count, created_at, author_id, submolt_id
- **`agents`** — id, name, first_seen, last_seen, post_count, comment_count
- **`comments`** — id, post_id, author_id, parent_id (threaded), content, created_at
- **`submolts`** — id, name, display_name, description, subscriber_count
- **`crawl_log`** — operation audit trail (kind, status, records_fetched, errors)
- **`sync_state`** — key-value store for crawl progress tracking

Analysis tables (stubs, currently empty): `topics`, `post_topics`, `post_summaries`, `agent_profiles`, `agent_relationships`, `trend_snapshots`.

## Reports

All reports are written in Chinese and generated from SQL queries against the dataset.

- **[REPORT.md](REPORT.md)** — Platform overview, growth curves, engagement pyramid, vote manipulation evidence, key findings
- **[Community Culture](reports/01-community-culture.md)** — 7 topic categories (philosophy, security, builders, crypto, humor, autonomy, Chinese community), narrative arcs, cross-community theme flows
- **[Agent Behavior](reports/02-agent-behavior.md)** — Agent classification (66.9% one-shot, 0.2% power users), top agent profiles, spam/bot detection, interaction network analysis
- **[Ecosystem Health](reports/03-ecosystem-health.md)** — Health scorecard (36/100), vote manipulation deep dive, community shell rate (87.2%), sustainability assessment

## Known Data Limitations

- **43.4% of posts** (30,700) missing author attribution — API stopped returning `author` field during incremental sync
- **Comments** only captured for Jan 30–31 — the `/posts/{id}/comments` endpoint started returning HTTP 405
- **Subscriber counts** mostly 0 — the list API does not populate this field for most communities

## License

Research and educational use.
