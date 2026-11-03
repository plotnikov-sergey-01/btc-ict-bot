# Mentor signal agent

The new system is an independent service inside this repository. It reuses the
existing Binance executor later, but does not call the old ICT strategy.

## Safety stages

1. **Collector:** store posts and media from an explicit channel allowlist.
2. **Observer:** vision model extracts a structured idea; no Binance keys.
3. **Paper approval:** Telegram buttons approve/reject an interpreted setup.
4. **Demo execution:** deterministic risk checks and Binance Demo orders.
5. **Optional automation:** only explicit, high-confidence setup classes.

Each trade must retain a link to the source channel/message, the model output,
market snapshot, selected entry/SL/TP, configuration version, and final result.

## Collector setup

Do not send Telegram credentials in chat and do not commit them.

```powershell
Copy-Item .env.mentor.example .env.mentor
```

Fill in:

- `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from the existing Telegram client.
- `TELEGRAM_SESSION` with the path/name of the existing Telethon session, or a
  new session name. A new session asks for phone/login confirmation once.
- `MENTOR_CHANNELS` with two comma-separated numeric channel IDs. Usernames are
  accepted for public channels, but IDs are more stable.

Install and start:

```powershell
pip install -r requirements.txt
python run_mentor_collector.py --env-file .env.mentor
```

New and edited posts are saved to `mentor_data/messages.sqlite3`; attached
images and documents are saved under `mentor_data/media/`. Both are ignored by
git. Albums retain the Telegram `grouped_id` so the analysis stage can process
their screenshots together. Reply links, the direct parent post, forwarding
metadata, and content hashes are retained. Cross-channel duplicates remain in
the audit log and will be grouped into one idea by the analysis stage.

The collector does not import old history automatically and cannot place
orders. Historical import and model analysis will be added after both channel
IDs are verified and a small real sample has been collected.

Inspect the latest collected records without opening SQLite manually:

```powershell
python run_mentor_inspect.py --env-file .env.mentor --limit 20
```

Analyze one message together with its reply chain. This calls OpenAI, spends API
credits, stores the structured result and usage, but never calls Binance:

```powershell
python run_mentor_analyze.py 7118 --env-file .env.mentor
```

The default model is `gpt-6.1-sol` with medium reasoning. Override it through
`OPENAI_MODEL` and `OPENAI_REASONING_EFFORT` in `.env.mentor`.

## Observation services

The collector queues only newly received or edited posts. The worker waits 90
seconds so Telegram albums can complete, analyzes the whole album/reply chain,
stores usage and results, and optionally sends an English summary through the
existing notification bot. It has no Binance execution path.

```bash
sudo cp deploy/systemd/mentor-collector.service /etc/systemd/system/
sudo cp deploy/systemd/mentor-analyzer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mentor-collector mentor-analyzer
journalctl -u mentor-collector -u mentor-analyzer -f
```

The default safety cap is 20 successful analyses per rolling 24 hours. Change
`MENTOR_MAX_ANALYSES_PER_DAY` in `.env.mentor` if needed.

## Planned structured idea

The analysis result will include source IDs, symbol, direction, setup status,
entry zone, invalidation, chart targets, timeframes, expiry, confidence, missing
facts, and evidence tied to text/images. A deterministic market-context stage
will then download Binance candles and calculate candidate swings, SL, TP, RR,
price deviation, and risk before asking for approval.
