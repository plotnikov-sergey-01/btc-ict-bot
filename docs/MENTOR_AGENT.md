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
their screenshots together.

The collector does not import old history automatically and cannot place
orders. Historical import and model analysis will be added after both channel
IDs are verified and a small real sample has been collected.

## Planned structured idea

The analysis result will include source IDs, symbol, direction, setup status,
entry zone, invalidation, chart targets, timeframes, expiry, confidence, missing
facts, and evidence tied to text/images. A deterministic market-context stage
will then download Binance candles and calculate candidate swings, SL, TP, RR,
price deviation, and risk before asking for approval.
