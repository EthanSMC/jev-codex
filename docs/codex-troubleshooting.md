# Codex handoff troubleshooting

## Chrome is running, but the daemon cannot connect

`DevToolsActivePort not found` is a discovery failure, not proof that Chrome is
closed or that remote debugging is disabled. Browser Harness 0.1.13 searches
known profile directories and probes ports 9222 and 9223. A custom profile using
another port can be reachable but undiscovered.

1. Inspect the running Chrome configuration and its remote-debugging settings.
   Obtain user authorization before enabling browser control.
2. For a known local endpoint, check its `/json/version` response. A valid
   response confirms discovery is reachable; it does not prove Jev can act.
3. Set `BU_CDP_URL` in the ignored `.env` to that endpoint. Do not copy a sample
   port without checking, expose it to the network, or print credentials.
4. If a harness daemon is already running, coordinate with its other users, then
   run `uv run --env-file .env browser-harness --reload` from the checkout.
   The next invocation starts the daemon with the new configuration.
5. Start Jev and run `uv run --env-file .env browser-harness --doctor` using the
   same checkout and environment. Verify a harmless browser action independently.

For example, `BU_CDP_URL=http://127.0.0.1:9224` worked in the live run below.
Ports and profiles can change when Chrome restarts. Keep the endpoint local and
recheck the actual configuration if this value stops working.

An optional cloud-auth failure in `--doctor` does not block local Chrome. The
relevant local checks are Chrome running, daemon alive, and an active connection.
Do not configure cloud billing or a text-model API to fix local CDP discovery.

## Repeated text requests without executed input

Each `text_request` describes a pending input, not a completed action. New request
IDs with an unchanged action count can indicate freshness retries. The current
fill path compares a whole-page marker after the conversation replies; visible
text and action semantics are part of that marker. Rotating ads or other dynamic
content can invalidate it even when the intended field still looks the same.

Reply only to the current request ID. If the run repeatedly makes no progress,
cancel the pending request with `cancel: true`, inspect already completed actions,
and consider a supported starting page or fallback. Do not weaken freshness
checks or reuse stale replies to force input. The current progress event does
not identify the stale-check reason, so changing content is evidence of a likely
cause, not a complete diagnosis by itself.

## Clicks that open a new tab

The browser adapter observes one owned CDP target. It does not adopt pop-up tabs.
A link can open a destination while the original page remains unchanged; the
agent can then choose the same link again and reach its no-progress stop.

Inspect tabs before another attempt. Use a fallback when following the new tab
is required, and verify the destination's URL and visible content. Do not report
an unchanged source page as proof that no browser side effect occurred.

## A successful result does not leave the page open

`Agent.__exit__` closes the owned target on success, failure, and interruption.
This makes the current CLI unsuitable on its own for a request to leave a page
in front of the user. Reopen a verified destination in a retained browser tab
and report that handoff. There is currently no `--keep-open` option.

## Live-run report: 2026-09-21

This is a manual field report from a Codex conversation, not a benchmark or a
checked-in recording. It intentionally excludes credentials and private profile
data. Environment: macOS 25.5.0, Chrome 153.0.8010.48, Python 3.12.12, Browser
Harness 0.1.13; Jev Codex runtime at commit `c07b9bf`.

| Observation | Interpretation and outcome |
| --- | --- |
| Initial startup reported `DevToolsActivePort not found`. Chrome used a custom profile and port 9224. | Standard discovery missed the running instance. After authorized remote-debugging setup, setting `BU_CDP_URL` and reloading the daemon restored connection. |
| Doctor reported Chrome running, daemon alive, and one active browser connection. | Local connection verified; optional cloud auth remained unconfigured. |
| Codex supplied `阿里巴巴`; the run reached Baidu results titled `阿里巴巴_百度搜索`. | Live text handoff and search worked. |
| A subsequent query produced repeated new `text_request` IDs while the execution count stayed at one; ad text changed between requests. | Consistent with whole-page freshness rejection. The pending input was cancelled; no instrumented stale-reason trace was captured. |
| A separate run clicked a search result three times, reported `page_changed: false` each time, then returned `blocked`. | The source target did not change. Pop-up navigation is unsupported; the particular Jev click destination was not independently verified. |
| A fallback browser reached `https://yunqi.aliyun.com/`, visibly titled `2026云栖大会首页-阿里云`, and retained the tab. | The overall user task was completed with a fallback, not solely by Jev. |

## Proposed code improvements

These are follow-up designs, **not implemented fixes**:

- Emit distinct connection-discovery errors and actionable endpoint diagnostics,
  without printing environment secrets.
- Emit stale-retry reasons and counts; stop repeated input starvation with a
  useful result. Any narrower fill guard must still validate document identity,
  target identity, editability, relevant form context, and hit testing. Preserve
  the rule that cached text requires identical helper context.
- Detect a newly created target associated with an executed click before
  considering another click. Define ownership and cleanup explicitly so the
  adapter never adopts or closes unrelated user tabs.
- Add an explicit keep-open/handoff mode, returning a verified destination and
  clear tab ownership while preserving cleanup as the default.

Tests for those changes should cover dynamic unrelated content, changed field or
form meaning, pop-up association, and successful/failed/interrupted tab cleanup.
Do not call paid APIs from tests or describe a partial live run as end-to-end
validation.
