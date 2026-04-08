import argparse
import json
import logging
import os
from datetime import datetime
from time import sleep, time
from typing import Dict, List, Optional
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

import config

try:
    from sllurp.llrp import LLRPReaderClient, LLRPReaderConfig, LLRPReaderState
    from sllurp.log import init_logging as init_sllurp_logging
except ImportError as exc:
    raise SystemExit(
        "Missing dependency: sllurp. Install it first with `pip install sllurp`."
    ) from exc

LOGGER = logging.getLogger("rfid_active_server")
EVENT_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reader_events.log")


def append_event_log(message: str) -> None:
    try:
        with open(EVENT_LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(message + "\n")
    except Exception:
        pass


def parse_antennas(raw: str) -> List[int]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if not values:
        return [1]
    return [int(item) for item in values]


def normalize_epc(epc_value) -> str:
    if isinstance(epc_value, bytes):
        return epc_value.decode("ascii", errors="ignore").upper()
    return str(epc_value).strip().upper()


def parse_rssi(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def as_tag_list(tags):
    if tags is None:
        return []
    if isinstance(tags, list):
        return tags
    if isinstance(tags, dict):
        return [tags]
    return []


class TagPrinter:
    def __init__(
        self,
        drop_stale_reports: bool,
        stale_grace_seconds: float,
        api_url: str,
        api_timeout_seconds: float,
        api_token: str,
    ):
        self.drop_stale_reports = drop_stale_reports
        self.stale_grace_seconds = stale_grace_seconds
        self.api_url = api_url.strip()
        self.api_timeout_seconds = api_timeout_seconds
        self.api_token = api_token.strip()
        self.session_start_epoch = 0.0
        self.skipped_stale = 0
        self.last_api_error_log = 0.0

    def begin_session(self):
        self.session_start_epoch = time()
        self.skipped_stale = 0

    def is_stale(self, tag: dict) -> bool:
        if not self.drop_stale_reports:
            return False
        last_seen_utc = tag.get("LastSeenTimestampUTC")
        if not isinstance(last_seen_utc, (int, float)):
            return False
        # Some readers send 0/non-epoch style timestamps; do not drop those.
        if last_seen_utc <= 0:
            return False
        tag_seen_epoch = last_seen_utc / 1_000_000.0
        # 2000-01-01 in epoch seconds. Older values are likely invalid clocks.
        if tag_seen_epoch < 946684800:
            return False
        return (tag_seen_epoch + self.stale_grace_seconds) < self.session_start_epoch

    def __call__(self, _reader, tags):
        rows = as_tag_list(tags)
        if not rows:
            LOGGER.debug("RO_ACCESS_REPORT received with 0 tags")
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for tag in rows:
            if not isinstance(tag, dict):
                LOGGER.debug("Unexpected tag payload type: %s", type(tag))
                continue

            if self.is_stale(tag):
                self.skipped_stale += 1
                if self.skipped_stale <= 5 or self.skipped_stale % 20 == 0:
                    LOGGER.info("Skipping stale buffered tag EPC=%s", normalize_epc(tag.get("EPC", "")))
                continue

            epc_value = tag.get("EPC") or tag.get("EPC-96") or tag.get("EPCData")
            epc = normalize_epc(epc_value)
            antenna = tag.get("AntennaID", "-")
            rssi_raw = tag.get("PeakRSSI", "-")
            rssi = parse_rssi(rssi_raw)
            seen_count = tag.get("TagSeenCount", 1)
            line = f"[{now}] TAG EPC={epc} ANT={antenna} RSSI={rssi_raw} COUNT={seen_count}"
            print(line, flush=True)
            append_event_log(line)
            if not epc:
                LOGGER.warning("Empty EPC received. Raw tag keys=%s", sorted(tag.keys()))
                continue
            self.push_to_app(epc, antenna, rssi)

    def push_to_app(self, epc: str, antenna, rssi) -> None:
        if not self.api_url:
            return

        payload_data = {
            "tag_id": epc,
            "antenna_id": str(antenna),
        }
        if isinstance(rssi, int):
            payload_data["rssi"] = rssi

        payload = json.dumps(payload_data).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["X-RFID-Token"] = self.api_token

        req = Request(self.api_url, data=payload, method="POST", headers=headers)

        try:
            with urlopen(req, timeout=self.api_timeout_seconds) as response:
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}")
        except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
            now = time()
            if now - self.last_api_error_log >= 5:
                self.last_api_error_log = now
                LOGGER.warning("API push failed for EPC=%s antenna=%s: %s", epc, antenna, exc)


def on_disconnected(reader):
    host, port = reader.get_peername()
    LOGGER.warning("Disconnected from reader %s:%s", host, port)


def on_inventory_state(_reader, _state):
    LOGGER.info("Reader state: INVENTORYING")


def build_reader(
    host: str,
    port: int,
    antennas: List[int],
    report_every_n_tags: int,
    report_timeout_ms: int,
    session: int,
    tx_power_dbm: Optional[Dict[int, float]],
    tag_printer: TagPrinter,
) -> LLRPReaderClient:
    cfg = {
        "antennas": antennas,
        "start_inventory": True,
        "disconnect_when_done": False,
        "reset_on_connect": True,
        "reconnect": False,
        "session": session,
        "keepalive_interval": 30000,
        "report_every_n_tags": report_every_n_tags,
        "report_timeout_ms": report_timeout_ms,
        "tag_content_selector": {
            "EnableROSpecID": False,
            "EnableSpecIndex": False,
            "EnableInventoryParameterSpecID": False,
            "EnableAntennaID": True,
            "EnableChannelIndex": False,
            "EnablePeakRSSI": True,
            "EnableFirstSeenTimestamp": False,
            "EnableLastSeenTimestamp": True,
            "EnableTagSeenCount": True,
            "EnableAccessSpecID": False,
            "C1G2EPCMemorySelector": {
                "EnableCRC": False,
                "EnablePCBits": False,
            },
        },
    }
    if tx_power_dbm:
        cfg["tx_power_dbm"] = tx_power_dbm

    reader_config = LLRPReaderConfig(cfg)

    reader = LLRPReaderClient(host, port, reader_config)
    reader.add_tag_report_callback(tag_printer)
    reader.add_disconnected_callback(on_disconnected)
    reader.add_state_callback(LLRPReaderState.STATE_INVENTORYING, on_inventory_state)
    return reader


def run_forever(
    host: str,
    port: int,
    antennas: List[int],
    reconnect_delay: float,
    report_every_n_tags: int,
    report_timeout_ms: int,
    session: int,
    antenna_cycle_seconds: float,
    drop_stale_reports: bool,
    stale_grace_seconds: float,
    api_url: str,
    api_timeout_seconds: float,
    api_token: str,
    tx_power_dbm: Optional[Dict[int, float]] = None,
) -> int:
    cycle_seconds = max(0.0, float(antenna_cycle_seconds or 0.0))
    cycle_mode = cycle_seconds > 0.0 and len(antennas) > 1
    print(
        f"Starting reader client for {host}:{port} on antennas {antennas} "
        f"tx_power_dbm={tx_power_dbm} cycle_seconds={cycle_seconds}",
        flush=True,
    )
    print("Press Ctrl+C to stop.", flush=True)

    while True:
        antenna_batches = [[ant] for ant in antennas] if cycle_mode else [antennas]
        had_cycle_error = False

        for active_antennas in antenna_batches:
            reader = None
            tag_printer = TagPrinter(
                drop_stale_reports=drop_stale_reports,
                stale_grace_seconds=stale_grace_seconds,
                api_url=api_url,
                api_timeout_seconds=api_timeout_seconds,
                api_token=api_token,
            )
            tag_printer.begin_session()

            try:
                reader = build_reader(
                    host,
                    port,
                    active_antennas,
                    report_every_n_tags=report_every_n_tags,
                    report_timeout_ms=report_timeout_ms,
                    session=session,
                    tx_power_dbm=tx_power_dbm,
                    tag_printer=tag_printer,
                )
                print(f"Connecting to reader {host}:{port} antennas={active_antennas}...", flush=True)
                reader.connect()
                print("Connected. Waiting for tags...", flush=True)

                if cycle_mode:
                    deadline = time() + cycle_seconds
                    while reader.is_alive() and time() < deadline:
                        reader.join(0.25)
                else:
                    while reader.is_alive():
                        reader.join(1)
                    LOGGER.warning("Reader worker thread ended.")

            except KeyboardInterrupt:
                print("\nStopping reader client...", flush=True)
                if reader:
                    try:
                        reader.disconnect(timeout=2)
                    except Exception:
                        pass
                LLRPReaderClient.disconnect_all_readers()
                return 0

            except Exception as exc:
                had_cycle_error = True
                LOGGER.error("Reader error: %s", exc)

            finally:
                if reader:
                    try:
                        if reader.is_alive():
                            reader.disconnect(timeout=2)
                    except Exception:
                        pass

        if cycle_mode and not had_cycle_error:
            continue

        print(f"Reconnecting in {reconnect_delay} seconds...", flush=True)
        try:
            sleep(max(reconnect_delay, 0))
        except KeyboardInterrupt:
            print("\nStopping reader client...", flush=True)
            LLRPReaderClient.disconnect_all_readers()
            return 0


def main() -> int:
    reader_cfg = config.RFID_ACTIVE_SETTINGS
    default_drop_stale = bool(reader_cfg.get("DROP_STALE_REPORTS", False))

    default_antennas = reader_cfg.get("ANTENNAS", [1, 2, 3, 4])
    if isinstance(default_antennas, str):
        default_antennas_str = default_antennas
    else:
        default_antennas_str = ",".join(str(a) for a in default_antennas)

    parser = argparse.ArgumentParser(
        description="Continuous RFID read from Zebra FX9600 (LLRP)."
    )
    parser.add_argument(
        "--host",
        default=reader_cfg.get("READER_HOST", "169.254.4.161"),
        help="FX9600 reader IP address",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(reader_cfg.get("READER_PORT", 5084)),
        help="LLRP port",
    )
    parser.add_argument(
        "--antennas",
        default=default_antennas_str,
        help='Comma-separated antenna IDs (example: "1,2,3,4")',
    )
    parser.add_argument(
        "--reconnect-delay",
        type=float,
        default=float(reader_cfg.get("RECONNECT_DELAY", 3.0)),
        help="Reconnect delay in seconds after disconnect/error",
    )
    parser.add_argument(
        "--report-every-n-tags",
        type=int,
        default=int(reader_cfg.get("REPORT_EVERY_N_TAGS", 1)),
        help="Issue a tag report every N tags",
    )
    parser.add_argument(
        "--report-timeout-ms",
        type=int,
        default=int(reader_cfg.get("REPORT_TIMEOUT_MS", 0)),
        help="Optional report timeout in milliseconds",
    )
    parser.add_argument(
        "--session",
        type=int,
        default=int(reader_cfg.get("SESSION", 0)),
        choices=[0, 1, 2, 3],
        help="Gen2 session",
    )
    parser.add_argument(
        "--allow-stale-reports",
        action="store_true",
        help="Print reader buffered old tags after connect",
    )
    parser.add_argument(
        "--stale-grace-seconds",
        type=float,
        default=float(reader_cfg.get("STALE_GRACE_SECONDS", 1.0)),
        help="Grace window for stale filtering in seconds",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable detailed LLRP debug logs",
    )
    parser.add_argument(
        "--api-url",
        default=reader_cfg.get("PUSH_URL", f"http://{config.APP_LOOPBACK_HOST}:{config.APP_PORT}/rfid-read"),
        help="Application endpoint for RFID push",
    )
    parser.add_argument(
        "--api-timeout-seconds",
        type=float,
        default=float(reader_cfg.get("API_TIMEOUT_SECONDS", 1.0)),
        help="HTTP timeout for API push in seconds",
    )
    parser.add_argument(
        "--api-token",
        default=str(reader_cfg.get("PUSH_TOKEN", "")),
        help="Optional shared token sent in X-RFID-Token header",
    )

    args = parser.parse_args()
    effective_debug = args.debug or bool(reader_cfg.get("DEBUG", False))
    init_sllurp_logging(debug=effective_debug, logfile=None)
    LOGGER.info(
        "Config host=%s port=%s antennas=%s session=%s report_n=%s timeout_ms=%s drop_stale=%s api_url=%s",
        args.host,
        args.port,
        args.antennas,
        args.session,
        args.report_every_n_tags,
        args.report_timeout_ms,
        (False if args.allow_stale_reports else default_drop_stale),
        args.api_url,
    )

    antennas = parse_antennas(args.antennas)
    return run_forever(
        args.host,
        args.port,
        antennas,
        args.reconnect_delay,
        args.report_every_n_tags,
        args.report_timeout_ms,
        args.session,
        float(reader_cfg.get("ANTENNA_CYCLE_SECONDS", 0.0)),
        (False if args.allow_stale_reports else default_drop_stale),
        args.stale_grace_seconds,
        args.api_url,
        args.api_timeout_seconds,
        args.api_token,
        (reader_cfg.get("TX_POWER_DBM") or None),
    )


if __name__ == "__main__":
    raise SystemExit(main())
