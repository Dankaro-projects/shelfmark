"""Whether the catalogue actually matches the disk — measured, not assumed.

"Fresh" must not mean "a refresh ran recently": that is a different claim
from "the index matches the disk", and it hides the file written two
minutes AFTER the last refresh. Only a walk can see that.

This lives outside server.py so the CLI can reach it. It used to be
MCP-only, which made the CLI structurally blind to drift: the only code
that compared catalogued rows against files on disk sat behind an agent
connection, so an operator who never ran an agent could not be told. A
catalogue once carried 19,157 phantom rows for four days while `refresh`
and `stats` both reported themselves healthy — `stats` and the hook
adapter now ask these same questions.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from .config import Config


def disk_drift(con, cfg: Config) -> dict:
    """Compare the catalogue against the filesystem, right now.

    Metadata only, no hashing — sub-second on a ~30k tree. Reuses the
    builder's walk so the skip rules and extra roots cannot drift apart
    from what actually gets indexed."""
    try:
        from .catalog import iso, walk
    except Exception as exc:                      # noqa: BLE001 - never break stats
        return {"state": "unknown", "why": f"cannot import catalog ({exc})"}

    try:
        known = {r["path"]: (r["bytes"], r["mtime"])
                 for r in con.execute("SELECT path, bytes, mtime FROM files")}
        seen, on_disk, new, modified = 0, set(), 0, 0
        for p, rel in walk(cfg):
            try:
                st = p.stat()
            except OSError:
                continue
            seen += 1
            on_disk.add(rel)
            prev = known.get(rel)
            if prev is None:
                new += 1
            elif prev[0] != st.st_size or prev[1] != iso(st.st_mtime):
                modified += 1
    except Exception as exc:                      # noqa: BLE001
        return {"state": "unknown", "why": f"walk failed ({exc})"}

    # Zero rows AND zero files seen is not "fresh" — it is a catalogue with
    # nothing in it, which answers every question with a confident
    # emptiness. "Matches an empty root" and "is a usable catalogue" are as
    # different as "a refresh ran" and "the index is current". The
    # threshold is exactly zero, deliberately: zero is a fact, while any
    # floor above it is a claim about corpus size that would demand a
    # config key. A near-empty catalogue is already covered — real files
    # appearing on disk show up in `new` below.
    if not known and seen == 0:
        return {"state": "empty",
                "missing_roots": [str(r.path) for r in cfg.roots
                                  if not r.path.is_dir()],
                "roots": [str(r.path) for r in cfg.roots]}

    # The OS can deny the document root to this process (macOS TCC does) and
    # os.walk swallows it, yielding an empty tree. That must never read as
    # "everything was deleted". The converse holds too: this ratio CANNOT
    # tell denial from deletion — freshness_line must assert neither.
    if known and seen < len(known) * 0.8:
        return {"state": "unreadable", "seen": seen, "rows": len(known)}

    return {"state": "ok", "new": new, "modified": modified,
            "deleted": len(known) - len(on_disk & known.keys())}


def freshness_line(con, cfg: Config, drift=None) -> str:
    """One line on whether the catalogue actually matches the disk.

    An index that silently stops updating still answers every query
    confidently, just from a frozen snapshot — the failure is invisible at
    the call site. So the MCP server reports this on every corpus_stats()
    call, and `shelfmark stats` prints it for the operator.

    `drift` lets a caller route the measurement through its own seam
    (server.py does, so its tests can fake the drift)."""
    if drift is None:
        drift = lambda c: disk_drift(c, cfg)     # noqa: E731

    ago, suffix, failed = "never", "", False
    status_path = cfg.status_path

    if not status_path.exists():
        failed, suffix = True, ("no REFRESH_STATUS.json — `shelfmark refresh` "
                                "has never run")
    else:
        try:
            st = json.loads(status_path.read_text(encoding="utf-8"))
            ts = datetime.strptime(st["finished_utc"], "%Y-%m-%dT%H:%M:%SZ") \
                         .replace(tzinfo=timezone.utc)
            # Clamped: a skewed clock must not produce "-34 min ago".
            age_h = max(0.0,
                        (datetime.now(timezone.utc) - ts).total_seconds() / 3600)
            ago = (f"{int(age_h * 60)} min ago" if age_h < 1
                   else f"{age_h:.1f} h ago" if age_h < 48
                   else f"{age_h / 24:.1f} days ago")
            # A failure on its 113th run is different news from a failure
            # on its 1st, and the reader can only act on that difference
            # if the line carries it.
            n = int(st.get("consecutive_failures") or 0)
            streak = (f" ({n} consecutive runs since "
                      f"{st.get('failing_since', '?')})" if n >= 2 else "")
            if st.get("state") == "degraded":
                # The run completed and the walk was full, but the prune
                # declined to remove rows it could not vouch for — the
                # catalogue knowingly lists files that are gone. Repeat the
                # detail until it is resolved: news that stops being news
                # is how this failure class once stayed quiet for days.
                suffix = (str(st.get("detail", "")).strip()
                          or "catalogue degraded") + streak
            elif st.get("state") != "ok":
                failed, suffix = True, (f"last refresh FAILED: "
                                        f"{st.get('detail', '?')}{streak}")
            elif age_h > cfg.stale_after_hours:
                failed, suffix = True, (
                    f"last clean refresh {ago} — whatever schedules "
                    f"`shelfmark refresh` is not firing")
            else:
                # The refresh reports a refused or skipped prune in here; a
                # plain "clean" is the only detail that carries no news.
                detail = str(st.get("detail", "")).strip()
                if detail and detail != "clean":
                    suffix = detail
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            failed, suffix = True, f"REFRESH_STATUS.json unreadable ({exc})"

    d = drift(con)

    if d["state"] == "empty":
        # The walk saw nothing and the catalogue holds nothing. Two causes,
        # and only one is distinguishable from here: a root that is not a
        # directory is named outright; roots that exist but yielded nothing
        # are either genuinely empty or entirely skip-ruled, and the check
        # cannot tell which.
        miss = d["missing_roots"]
        if miss:
            head = (f"⚠ CATALOGUE EMPTY — configured root"
                    f"{'s do not exist' if len(miss) > 1 else ' does not exist'}"
                    f": {', '.join(miss)}")
            tail = ("Point [[roots]] in config.toml at the folders that "
                    "hold your documents, or mount the missing one.")
        else:
            head = (f"⚠ CATALOGUE EMPTY — "
                    f"{', '.join(d['roots'])} exists but holds no "
                    f"indexable files")
            tail = ("Either it is genuinely empty, or everything under it "
                    "is excluded by the skip rules. Point [[roots]] in "
                    "config.toml at the folders that hold your documents.")
        return f"{head}. {tail}" + (f" [{suffix}]" if suffix else "")

    if d["state"] == "unreadable":
        # The ratio behind this state has at least three causes — an OS
        # denial (macOS TCC), an unmounted filesystem, a real mass move or
        # deletion — and the measurement cannot tell them apart. A renamed
        # root looks identical from here. So name the possibilities and
        # give each its remedy; asserting the denial story once blamed
        # macOS permissions for a folder rename, four days running. The
        # remedy is platform-specific because this line is read by a person
        # on ONE platform — naming TCC on Linux is advice written for
        # somebody else.
        remedy = ("grant this process access (macOS: Full Disk Access)"
                  if sys.platform == "darwin" else
                  "check the root's permissions, and that its filesystem "
                  "is mounted")
        return (f"⚠ CANNOT VERIFY — the walk saw only {d['seen']:,} of "
                f"{d['rows']:,} catalogued files. Either the root is "
                f"unreadable to this process, or that many files really "
                f"went away — a renamed or moved root looks identical from "
                f"here. Answers come from the last good snapshot; {remedy}, "
                f"or if the files really moved, run `shelfmark refresh "
                f"--force`." + (f" [{suffix}]" if suffix else ""))

    if d["state"] == "unknown":
        head = "⚠ freshness UNKNOWN" if not failed else "⚠ LAST REFRESH FAILED"
        return f"{head} — {d['why']}. {suffix}".strip()

    behind = [f"{d[k]} {k}" for k in ("new", "modified", "deleted") if d[k]]

    if behind:
        return (f"⚠ INDEX BEHIND DISK — {', '.join(behind)} vs the catalogue "
                f"(last refresh {ago}). Run `shelfmark refresh`."
                + (f" [{suffix}]" if suffix else ""))

    if failed:
        return (f"⚠ index matches disk, but {suffix}. Rights and "
                f"confidentiality may not have been re-applied — run "
                f"`shelfmark refresh`.")

    if suffix:
        # A non-"clean" detail is a refused or skipped prune. The rows agree
        # with disk this second, so not an error — but not a plain tick.
        return f"~ index matches disk, last refresh {ago} — but {suffix}"

    return f"✓ index fresh — matches disk, last refresh {ago}"
