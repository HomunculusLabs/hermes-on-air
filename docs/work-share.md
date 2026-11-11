# Work view

For a complete project session, see [Live build](live-build.md); automatic model-written relay
notes are not supported by the current owner-word relay contract.

Work view is a public, text-only page the owner agent writes to. It uses the **existing presentation Browser Source**, called `slides` in Views; it is not a screen capture, terminal capture, file reader, or a new OBS input. Viewers see only explicitly published text. Use the owner-selected [window share](views.md#share-one-window) instead when the audience needs to see an actual application and its interaction. The owner selects and, where required, confirms that window.

In the local owner chat, try: “Share what you are working on in the small box.” The agent calls `onair_work_share` to show the relevant function or test output, then `onair_layout` to put the `slides` source in a sub box. Example:

```json
onair_work_share {"action":"show","title":"Fix the clamp function","status":"running tests","panes":[{"kind":"code","label":"clamp.py","language":"python","content":"def clamp(value, maximum):\n    return min(value, maximum)"},{"kind":"terminal","label":"Tests","content":"pytest -q\n1 passed"}]}
onair_layout {"action":"put","slot":"sub1","source":"slides"}
```

Configure the owned OBS sources first with `onair_obs_setup` `configure`. A layout change does not switch the program scene or prove pixels reached viewers; check OBS preview. Use `onair_obs_scene` to switch to the owned Boxes scene when requested. A source moves rather than duplicates. The page reserves the same default caption band and right inset region as slides; other layouts or customized captions can overlap it.

## Actions and limits

All calls use `onair_work_share`; extra fields are refused, including nested pane fields. Each successful mutation returns a revision, one-line summary and public program snapshot. Only one of slides, standby and work is visible at a time. Publishing a deck or navigating slides switches away from work; updating work brings it back. `onair_present_status` reports the current public mode. Routine work event notifications are coalesced to at most four per second; an urgent clear or privacy cut is not delayed. Server snapshots can briefly lead a connected page while updates are coalesced.

| Action | Fields and effect |
|---|---|
| `show` | `title` (required, one line, 80 characters maximum); `panes` (one or two). Each pane has required `kind` (`code`, `terminal`, `text`, or `diff`), `label` (40 characters), `content` (6000 characters and 120 lines), plus optional `language` (24 characters). Optional `status` is one line, 80 characters. Replaces the work view. |
| `update` | `pane` (0 or 1) and `content`: replace the indicated pane. |
| `append` | `pane` and `text`: append to a terminal or text pane, retaining the last 120 lines. The resulting content must still fit 6000 characters. |
| `status` | `status`: replace the status line. |
| `step` | `done` and `next` are arrays, and `current` is a string. At most six items in total; each is at most 60 characters. The rail shrinks long lists to fit, so keep labels short for small boxes. |
| `clear` | No other fields. Discard work and return to the last deck or the standby card. |
| `read` | No other fields. Read the current public program snapshot; a connected browser may be up to one coalescing interval behind. |

```json
onair_work_share {"action":"append","pane":1,"text":"\nFAIL: expected 10, got 12"}
onair_work_share {"action":"step","done":["Reproduce failure"],"current":"Fix function","next":["Run tests"]}
onair_work_share {"action":"update","pane":0,"content":"def clamp(value, maximum):\n    return min(value, maximum)"}
onair_work_share {"action":"status","status":"tests passing"}
onair_work_share {"action":"read"}
onair_work_share {"action":"clear"}
```

The human can type `/onair-present work status` or `/onair-present work clear` in a local owner session without the model. The tool has the same owner-session restrictions as slide publishing; messaging sessions and the public stage cannot use it.

## Safety and privacy

Every text field is normalized and checked before publication. ANSI control sequences are stripped; other control characters are refused. Home-directory paths, private-network addresses, key-like secrets, e-mail addresses, secret-looking assignments (such as a token or password variable), and `Authorization:` headers are refused. The error identifies the field, line and rule without repeating the value. This is a narrow backstop, **not a guarantee that text is safe**: inspect content before sharing and never paste environment dumps, credentials, private logs or unrelated personal data. All content is public when visible. The page creates text nodes, not HTML, and does not fetch user-provided URLs. The work tool reads no files.

Work is blank unless the configured captions/voice overlay provides a valid live, non-private state. Privacy on, off-air, malformed state, a disconnected overlay or an unavailable overlay blank work, including the public work snapshot. The presentation surface follows the configured overlay URL (or the running private show when automatically selected). Make sure this is the same show the owner controls. If the overlay is missing, start the intended show or configure its overlay; work stays blank rather than assuming privacy is off. A network or browser disconnect can introduce transport delay, and this is **not** a whole-OBS privacy guarantee: other OBS sources and microphones have their own controls. Check the real OBS output.

Image sharing is not supported: the plugin does not read or display an image path, and there is no image confirmation command. Pixels cannot be checked by the text filter.
