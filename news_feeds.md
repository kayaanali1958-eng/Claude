# Fast News Feeds

news-analyst and policy-watch read these feeds directly (WebFetch) on every run, before any web search. Feeds update within seconds to minutes of publication. Search engines can take much longer to index a story.

Only items newer than the agent's last check matter. Note each item's publish time.

## Official sources (primary, most reliable)
| Feed | URL | Use |
|---|---|---|
| Federal Reserve press releases | https://www.federalreserve.gov/feeds/press_all.xml | Fed statements, speeches, surprise actions |
| White House news | https://www.whitehouse.gov/news/feed/ | Executive orders, tariffs, presidential actions |
| BLS latest releases | https://www.bls.gov/feed/bls_latest.rss | CPI, jobs, PPI the moment they post |
| SEC insider filings (Form 4) | https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&output=atom | Insider buys/sells, live as filed |

## Headlines (fast, check against a second source before acting)
| Feed | URL | Use |
|---|---|---|
| Google News: last hour, Trump + markets | https://news.google.com/rss/search?q=Trump+tariff+OR+%22Truth+Social%22+OR+%22White+House%22+markets+when:1h&hl=en-US&gl=US&ceid=US:en | Presidential posts and remarks as covered by the press |
| Google News: last hour, stock market | https://news.google.com/rss/search?q=stock+market+OR+Nasdaq+OR+%22S%26P+500%22+when:1h&hl=en-US&gl=US&ceid=US:en | Breaking market headlines |
| Google News: last hour, Fed | https://news.google.com/rss/search?q=Federal+Reserve+OR+Powell+when:1h&hl=en-US&gl=US&ceid=US:en | Fed headlines |
| Yahoo Finance | https://finance.yahoo.com/news/rssindex | General market news |
| PR Newswire | https://www.prnewswire.com/rss/news-releases-list.rss | Company press releases (earnings, deals) |

Blocked or unreliable from the cloud test (may work from a laptop): CNBC RSS (403), Treasury press releases (timed out), MarketWatch (redirects).

## Live listener (fastest; runs on your laptop)
`scripts/news_listener.py` streams Alpaca real-time news and polls key X accounts, writing to `news/live.jsonl`. The timer starts it automatically when `.env` has keys and copies the newest 200 items to `news/latest.jsonl` each run. Setup: copy `.env.example` to `.env`, add keys, then `pip install websockets`.

## Faster still (needs an account; optional)
- **X API** (paid): read @WhiteHouse, @POTUS and the President's account directly. Add an X MCP server to Claude Code; policy-watch uses `mcp__x__*` tools first if present.
- **Benzinga / Finnhub / Alpaca news API**: wire-speed headlines. Add as an MCP server or a feed URL here.
