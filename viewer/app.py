"""Streamlit UI for Moltbook Community Observatory."""

import streamlit as st
from pathlib import Path
from datetime import datetime

# Add project root to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Config
from store.db import Database

config = Config()


@st.cache_resource
def get_db():
    db = Database(config.db_path)
    db.connect()
    return db


def format_date(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso_str


# --- Sidebar navigation ---
st.set_page_config(page_title="Moltbook Observatory", layout="wide")

page = st.sidebar.radio(
    "Navigation",
    ["Post Feed", "Agent Profiles", "Submolt Explorer", "Conversation Explorer", "Trends Dashboard", "Crawl Status"],
)

db = get_db()


# ============================================================
# Page: Post Feed
# ============================================================
def page_post_feed():
    st.title("Post Feed")

    col1, col2, col3 = st.columns(3)
    with col1:
        sort = st.selectbox("Sort by", ["new", "hot", "comments"])
    with col2:
        submolts = db.get_submolts(limit=200)
        submolt_options = {"All": None} | {s["name"]: s["id"] for s in submolts}
        submolt_filter = st.selectbox("Submolt", list(submolt_options.keys()))
        submolt_id = submolt_options[submolt_filter]
    with col3:
        agent_search = st.text_input("Filter by agent name")
        author_id = None
        if agent_search:
            agent = db.get_agent_by_name(agent_search.strip())
            if agent:
                author_id = agent["id"]
            else:
                st.warning("Agent not found")

    total = db.count_posts(submolt_id=submolt_id, author_id=author_id)
    page_size = 20
    total_pages = max(1, (total + page_size - 1) // page_size)
    page_num = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1)
    offset = (page_num - 1) * page_size

    st.caption(f"Showing {offset + 1}-{min(offset + page_size, total)} of {total} posts")

    posts = db.query_posts(sort=sort, submolt_id=submolt_id, author_id=author_id, limit=page_size, offset=offset)

    for p in posts:
        score = p["upvotes"] - p["downvotes"]
        with st.container(border=True):
            st.markdown(f"**{p['title']}**")
            meta = f"by **{p.get('author_name', '?')}** in **{p.get('submolt_display_name') or p.get('submolt_name', '?')}** | "
            meta += f"Score: {score} ({p['upvotes']}↑ {p['downvotes']}↓) | "
            meta += f"Comments: {p['comment_count']} | {format_date(p['created_at'])}"
            st.caption(meta)
            if p.get("content"):
                with st.expander("Content"):
                    st.write(p["content"][:2000])
            if p.get("url"):
                st.markdown(f"[Link]({p['url']})")


# ============================================================
# Page: Agent Profiles
# ============================================================
def page_agent_profiles():
    st.title("Agent Profiles")

    tab1, tab2 = st.tabs(["Search", "Leaderboard"])

    with tab1:
        query = st.text_input("Search agents by name")
        if query:
            agents = db.search_agents(query, limit=20)
            if not agents:
                st.info("No agents found.")
            for a in agents:
                with st.container(border=True):
                    st.markdown(f"**{a['name']}**")
                    st.caption(f"Posts: {a['post_count']} | Comments: {a['comment_count']} | First seen: {format_date(a['first_seen'])}")

        agent_name = st.text_input("View agent detail (exact name)")
        if agent_name:
            agent = db.get_agent_by_name(agent_name.strip())
            if agent:
                st.subheader(agent["name"])
                c1, c2, c3 = st.columns(3)
                c1.metric("Posts", agent["post_count"])
                c2.metric("Comments", agent["comment_count"])
                c3.metric("First Seen", format_date(agent["first_seen"]))

                st.markdown("#### Recent Posts")
                posts = db.query_posts(author_id=agent["id"], limit=10)
                for p in posts:
                    st.markdown(f"- **{p['title']}** ({format_date(p['created_at'])})")
            else:
                st.warning("Agent not found")

    with tab2:
        order = st.selectbox("Rank by", ["post_count", "comment_count"])
        top = db.get_top_agents(order_by=order, limit=20)
        for i, a in enumerate(top, 1):
            st.markdown(f"{i}. **{a['name']}** — {a[order]} {order.replace('_', ' ')}")


# ============================================================
# Page: Submolt Explorer
# ============================================================
def page_submolt_explorer():
    st.title("Submolt Explorer")

    submolts = db.get_submolts(order_by="subscriber_count", limit=100)

    col1, col2 = st.columns([1, 2])
    with col1:
        st.subheader("Top Submolts by Subscribers")
        for s in submolts[:20]:
            st.markdown(f"- **{s.get('display_name') or s['name']}** ({s['subscriber_count']} subscribers)")

    with col2:
        name = st.text_input("View submolt detail (name)")
        if name:
            s = db.get_submolt_by_name(name.strip())
            if s:
                st.subheader(s.get("display_name") or s["name"])
                st.caption(f"/{s['name']} — {s['subscriber_count']} subscribers")
                if s.get("description"):
                    st.write(s["description"])

                st.markdown("#### Recent Posts")
                posts = db.query_posts(submolt_id=s["id"], limit=10)
                for p in posts:
                    score = p["upvotes"] - p["downvotes"]
                    st.markdown(f"- **{p['title']}** by {p.get('author_name', '?')} (score: {score}, comments: {p['comment_count']})")
            else:
                st.warning("Submolt not found")


# ============================================================
# Page: Conversation Explorer
# ============================================================
def page_conversation_explorer():
    st.title("Conversation Explorer")

    post_id = st.text_input("Enter Post ID")
    if not post_id:
        st.info("Enter a post ID to explore its comment thread.")
        st.markdown("#### Recent posts with comments")
        posts = db.query_posts(sort="comments", limit=10)
        for p in posts:
            st.markdown(f"- `{p['id']}` — **{p['title']}** ({p['comment_count']} comments)")
        return

    post, comments = db.get_post_with_comments(post_id.strip())
    if not post:
        st.error("Post not found.")
        return

    st.subheader(post["title"])
    st.caption(f"by **{post.get('author_name', '?')}** | {format_date(post['created_at'])} | Score: {post['upvotes'] - post['downvotes']}")
    if post.get("content"):
        st.write(post["content"])

    st.markdown(f"---\n#### Comments ({len(comments)})")

    # Build comment tree
    by_parent: dict[str | None, list] = {}
    for c in comments:
        parent = c.get("parent_id")
        by_parent.setdefault(parent, []).append(c)

    def render_tree(parent_id: str | None, depth: int = 0):
        children = by_parent.get(parent_id, [])
        for c in children:
            indent = "&nbsp;" * (depth * 6)
            author = c.get("author_name", "?")
            st.markdown(
                f"{indent}**{author}** <small>{format_date(c['created_at'])}</small><br/>"
                f"{indent}{c.get('content', '')}",
                unsafe_allow_html=True,
            )
            render_tree(c["id"], depth + 1)

    render_tree(None)


# ============================================================
# Page: Trends Dashboard
# ============================================================
def page_trends_dashboard():
    st.title("Trends Dashboard")

    st.subheader("Daily Post Activity")
    post_counts = db.get_daily_counts("posts", days=60)
    if post_counts:
        import pandas as pd
        df = pd.DataFrame(post_counts)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        st.line_chart(df.set_index("date")["count"])
    else:
        st.info("No post data available.")

    st.subheader("Daily Comment Activity")
    comment_counts = db.get_daily_counts("comments", days=60)
    if comment_counts:
        import pandas as pd
        df = pd.DataFrame(comment_counts)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        st.line_chart(df.set_index("date")["count"])
    else:
        st.info("No comment data available.")

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Top Posters")
        top_posters = db.get_top_agents(order_by="post_count", limit=10)
        for i, a in enumerate(top_posters, 1):
            st.markdown(f"{i}. **{a['name']}** — {a['post_count']} posts")

    with col2:
        st.subheader("Top Commenters")
        top_commenters = db.get_top_agents(order_by="comment_count", limit=10)
        for i, a in enumerate(top_commenters, 1):
            st.markdown(f"{i}. **{a['name']}** — {a['comment_count']} comments")


# ============================================================
# Page: Crawl Status
# ============================================================
def page_crawl_status():
    st.title("Crawl Status")

    stats = db.get_stats()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Agents", f"{stats['agents']:,}")
    c2.metric("Submolts", f"{stats['submolts']:,}")
    c3.metric("Posts", f"{stats['posts']:,}")
    c4.metric("Comments", f"{stats['comments']:,}")

    st.markdown("---")
    last_sync = db.get_sync_state("posts_last_sync", "Never")
    crawl_status = db.get_sync_state("crawl_status", "idle")
    st.markdown(f"**Last sync:** {last_sync}")
    st.markdown(f"**Crawl status:** {crawl_status}")

    st.subheader("Crawl Logs")
    logs = db.get_crawl_logs(limit=20)
    if logs:
        import pandas as pd
        df = pd.DataFrame(logs)
        display_cols = ["id", "kind", "status", "records_fetched", "errors", "started_at", "finished_at"]
        available = [c for c in display_cols if c in df.columns]
        st.dataframe(df[available], use_container_width=True)
    else:
        st.info("No crawl logs yet.")


# ============================================================
# Router
# ============================================================
pages = {
    "Post Feed": page_post_feed,
    "Agent Profiles": page_agent_profiles,
    "Submolt Explorer": page_submolt_explorer,
    "Conversation Explorer": page_conversation_explorer,
    "Trends Dashboard": page_trends_dashboard,
    "Crawl Status": page_crawl_status,
}

pages[page]()
