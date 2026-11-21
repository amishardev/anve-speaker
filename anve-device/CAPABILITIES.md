# What the assistant can do from this speaker: tested

Tested 16 Nov 2026, 23:43, against `app.anveai.com` with the room device key, engine
`live` (Gemini Live). Each request was spoken by a synthesized voice and sent through
push-to-talk by `tools/ask.py`, in one session, so this tests the assistant and the
protocol, not the microphone. The full list of 77 tools, read from the server code, is in
the build notes (`notes/capabilities.md` in the build folder).

Room device: a spoken "yes" is never accepted. Anything that needs confirmation waits on
the owner's ANVE screen.

## Read-only: 15 of 15 answered, every tool call succeeded

| Category | Said | Tool the assistant ran | Result | Server latency |
|---|---|---|---|---|
| Capabilities | What can you do? | `what_can_you_do` | Listed documents, meetings, finance, GST, reminders, decks, sheets, research, agents | 1.9 s |
| General knowledge | Why is the sky blue? | none | Correct one-sentence answer | 2.3 s |
| Live answer | What is the weather in Sambalpur tomorrow? | `web_answer` | **Wrong place: it read out Istanbul's weather** (source haberler.com), though it heard "Sambalpur" correctly | 4.5 s |
| Calendar | What is on today? | `whats_on_today` | Overdue items for a client, next GST date, approvals waiting | 2.2 s |
| Reminders | What is due this week? | `list_due` | 1 due in 7 days, 8 overdue | 2.1 s |
| Timers | Which timers are running? | `list_timers` | None set | 5.7 s |
| Documents | List my documents. | `list_documents` | None yet | 2.3 s |
| Sheets | List my spreadsheets. | `list_sheets` | None yet | 1.9 s |
| Meetings | List my meetings. | `list_meetings` | 3 meetings, minutes approved | 3.1 s |
| Notes | List my notes. | `list_notes` | 2 notebooks, named | 2.1 s |
| Agents | List my agents. | `list_agents` | 19 agents | 2.1 s |
| Agents | Is anything waiting for my approval? | `pending_approvals` | 6 waiting | 2.2 s |
| Finance | Which invoices are overdue? | `finance_overdue` | None overdue | 2.2 s |
| Finance | When is the next GST due date? | `gst_due_dates` | GSTR-3B for October, due 20 November | 2.8 s |
| Workspace | What does our leave policy say? | `search_workspace` | Found matching documents but refused to read them aloud: "the details will be on your screen" (the room-device rule, enforced by the server) | 4.3 s |

Notes:
- The Istanbul answer is the server's web search going wrong, not the speaker: the
  request reached the model intact. In the live session by voice, the same tool answered
  for Sambalpur and Kanpur correctly.
- The transcript the server shows for a request can be far from what was said ("Which
  invoices are overdue?" was shown as "What's the voice of our father, dude?") while the
  right tool still runs. The model works from the audio; the transcript is a separate
  guess.
- Every session opens with the account's waiting approvals.

## Requests that make the assistant ask for confirmation

Run 16 Nov 2026, 23:47: 7 of 7 answered, no tool failed. Nothing was confirmed, so
nothing was created. Every read-back came with `voice_ok` false, as a room device should.

| Said | Tool | What ANVE did |
|---|---|---|
| Save a note: call the auditor on Monday. | `save_note` | Read-back: save a note in Notebook > Assistant notes |
| Draft a leave application for next Monday. | `make_document` | Asked first: your name, who it goes to (no read-back yet) |
| Make a monthly budget spreadsheet. | `make_sheet` | Read-back: make it from the "Monthly budget" template |
| Make a deck about our GST services for new clients. | `make_deck` | Read-back: a new deck, takes a few minutes |
| Make an image of a lighthouse at sunset. | `generate_image` | Read-back: drawn by a cloud image model |
| Record this meeting. | `start_recording` | Read-back: start recording "Current Meeting" on this device |
| Create an agent that summarises overdue invoices every Friday. | `create_agent` | Drafted it and asked the builder's questions |

On the spreadsheet request the server's own honesty check fired once ("the reply said
something was done, but no tool confirmed it. Nothing was done").

## Requests that act at once (no read-back)

Not run. They set a real timer, add a real reminder and send a real email to the owner,
so they wait for the owner's go-ahead: `tools/ask.py --suite acts --yes-acts`.
