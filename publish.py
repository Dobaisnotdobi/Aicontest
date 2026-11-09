import html
import json
import re
import subprocess
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from naver_cafe import access_token, publish, transport

KST = timezone(timedelta(hours=9))
HISTORY = Path("history.json")
BOARD = {"club_id": 31813012, "menu_id": 15}


def load(path):
    return Path(path).read_text(encoding="utf-8-sig")


def save(state):
    HISTORY.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for args in (["add", "history.json"], ["commit", "-m", "Record contest publication status"], ["push", "origin", "HEAD:main"]):
        subprocess.run(["git", *args], check=True)


def normalized(title):
    text = unicodedata.normalize("NFKC", html.unescape(title)).lower()
    text = re.sub(r"^\s*\[[^\]]*\]", "", text)
    return re.sub(r"20\d{2}년?|[^a-z0-9가-힣]", "", text)


def board_titles():
    titles = set()
    page = 1
    while True:
        url = f"https://apis.naver.com/cafe-web/cafe-boardlist-api/v1/cafes/31813012/menus/15/articles?page={page}&perPage=50"
        result = json.loads(transport(url))["result"]
        for row in result["articleList"]:
            article = row.get("item", row)
            titles.add(normalized(article["subject"]))
        info = result["pageInfo"]
        if page >= info["lastNavigationPageNumber"] and not info["visibleNextButton"]:
            return titles
        page += 1


def load_poster(item):
    if not item.get("poster_url"):
        return ("poster.jpg", "image/jpeg", Path(item["poster"]).read_bytes())
    parsed = urlsplit(item["poster_url"])
    if parsed.scheme != "https" or parsed.hostname != "postfiles.pstatic.net" or parsed.username or parsed.password or parsed.port:
        raise ValueError("Poster URL must be a reviewed official Naver blog image.")
    data = transport(item["poster_url"], limit=6_000_000)
    if data.startswith(bytes.fromhex("89504e470d0a1a0a")):
        return ("poster.png", "image/png", data)
    if data.startswith(bytes.fromhex("ffd8ff")):
        return ("poster.jpg", "image/jpeg", data)
    raise ValueError("Poster is not a PNG or JPEG image.")


def main():
    state = json.loads(load(HISTORY))
    if state.get("pending"):
        raise SystemExit("Unconfirmed post: check the board before retrying.")
    queue = sorted(json.loads(load("queue.json")), key=lambda item: item["cutoff"])
    token = access_token()
    for item in queue:
        now = datetime.now(KST)
        cutoff = datetime.fromisoformat(item["cutoff"])
        if (cutoff.date() - now.date()).days < 3 or now >= cutoff:
            continue
        if any(row["id"] == item["id"] or row["official_url"] == item["official_url"] for row in state["publication_history"]):
            continue
        key = normalized(item["title"])
        if any(key in title or title in key for title in board_titles() if title):
            continue
        draft = {"subject": item["title"], "content": "".join("<p>" + html.escape(item[field]) + "</p>" for field in ("period", "body", "official_url"))}
        poster = load_poster(item)
        state["pending"] = {"id": item["id"], "title": item["title"], "started": now.isoformat()}
        save(state)
        article = publish(draft, poster, token, BOARD)
        state["publication_history"].append({**state["pending"], "official_url": item["official_url"], "article_id": article["article_id"], "url": article["url"], "posted": datetime.now(KST).isoformat()})
        state["pending"] = None
        save(state)
        print(article["url"], flush=True)
        time.sleep(60)


if __name__ == "__main__":
    main()
