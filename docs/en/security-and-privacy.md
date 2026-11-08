[Português (Brasil)](../security-and-privacy.md) · **English**

# Read this first: radio is not private

Everything in this guide rides on cheap analogue FM handhelds. **They were
never designed for privacy or security, and nothing in this repo changes
that.** Design your agent as if every word it hears or says is broadcast
to strangers and recorded, because it is.

## What the radio does not give you

| You might assume | Reality |
|---|---|
| "Only my radios hear us." | Analogue FM is unencrypted. Any receiver or scanner in range (often several kilometres, more from high ground) hears both sides. A €20 SDR can record the channel around the clock. |
| "The CTCSS/DCS tone keeps others out." | A tone only decides when **your** radio opens its squelch. Everyone else on the frequency still hears you. The tone is sent with every transmission, any scanner decodes it, and anyone can set the same tone and **talk to your agent**. It is a noise filter, not a password. |
| "The radio's *scramble* setting encrypts." | Voice-inversion scrambling is trivially reversible and is not encryption. On many bands it is also not allowed. |
| "I'll just encrypt it." | On amateur bands most countries forbid obscuring the meaning of a transmission. Licence-free bands (FRS, PMR446 and similar) require type-approved, unmodified equipment. Plan as if encryption is not an option. |
| "A spoken password or 'yes' confirms it's me." | A spoken password is broadcast and can be recorded and **replayed**. A spoken "yes" proves nothing about who said it. |

## Don't use the factory channels

Every unit of the same model ships with the **same** default channel list.
BF-888/BF-777S class radios are sold by the hundreds of thousands, so those
frequencies are busy. Other owners, toy radios, building sites and shops all
sit on them, and your agent will hear their traffic and answer it.

- Pick a frequency that is **legal for you** (licence class, band, power,
  type approval) and quiet where you are. Listen to it for a few days first;
  the [scanner](10-scanner-dashboard.md) helps.
- Set a **non-default** CTCSS/DCS tone on both the base radio and your
  handhelds. It cuts noise and accidental triggers, but it is not security.
- Some factory defaults are not legal to transmit on in many countries.
  Program the radio yourself ([chapter 9](09-chirp-programming.md) shows how
  to read it back and check).
- Recording or disclosing other people's radio traffic is restricted in some
  countries. Keep the scanner's recording feature for your own channels.

## Brazilian FRS-style channels

Here, “Brazilian FRS” means Anatel's **Equipamento de Radiocomunicação de
Uso Geral**, not US FRS authorisation. [Ato 14.448/2017, section 15,
Table XII](https://informacoes.anatel.gov.br/legislacao/atos-de-certificacao-de-produtos/2017/1139-ato-14448) lists 26 channels:
462.5625–462.7125 MHz and 467.5625–467.7125 MHz, in 12.5 kHz steps in each
group; maximum 500 mW ERP and 12.5 kHz occupied bandwidth. Use the Brazilian
plan, not imported channel numbers or arbitrary frequencies between channels.

Use equipment homologated by Anatel for the intended operation. Programming
a BF-777S/BF-888 to these channels or choosing “Low” power does not establish
compliance. Section 15 also restricts one-way transmissions and prohibits
interconnection to networks supporting collective-interest telecommunications
services. Verify that the proposed radio-to-agent setup is permitted before
transmitting; the channel list alone does not authorise this bridge.

## Separate RX and TX: a small obstacle for casual listeners

Where equipment approval and operating rules permit, choose **different RX
and TX channels** independently from quiet, permitted channels. Avoid a
conventional repeater offset or predictable channel-number pairing. Hearing
one frequency then does not reveal the other through the usual offset
convention. This may discourage casual listeners or attempts to talk back;
it cannot make the pair unguessable.

A and B below stand for your chosen channels:

| Radio | RX (receives) | TX (transmits) |
|---|---|---|
| Base connected to Hermes | A | B |
| Each handheld | B | A |

Operation remains half-duplex: speak, release PTT, then listen. These
handhelds do not hear one another directly. Both frequencies must be
permitted for transmission by the corresponding radio; check activity on
both before use. Match each transmitter's CTCSS/DCS tone to its receiver.

In CHIRP, if the model driver supports it, set `Frequency` to RX,
`Duplex = split`, and `Offset` to the **absolute TX frequency**. Reverse
RX/TX on the handhelds, then read back and verify both directions. The
[chapter 9 backup helper](09-chirp-programming.md) only reads memory;
it cannot apply these settings.

A scanner or SDR can discover both frequencies. Anyone who finds the base's
RX frequency and tone can still address the agent. Keep all data and tool
restrictions below: split channels provide obscurity, not encryption or
authentication.

## Data privacy: assume the whole neighbourhood listens

Anything the agent says is published. Anything said to it is published too,
and is then also sent to your STT and LLM providers and stored on disk.

**Never let a tool return, or the agent speak:**

- personal data of anyone: full names with context, addresses, phone
  numbers, e-mails, health or financial details, order/customer records;
- credentials, codes and tokens: passwords, PINs, one-time codes, return or
  pickup codes, API keys, Wi-Fi passwords;
- operational security: "the shop is empty", alarm state, schedules, where
  valuables or vehicles are, when nobody is home.

What to do instead:

- Write tools that return the **minimum**: counts, statuses, first names or
  initials, "yes/no". Make the *tool* refuse sensitive fields. Don't rely on
  the prompt to make the model hold back. Prompts leak; filters in code don't.
- Keep secrets out of the agent's reach entirely, so it cannot be talked into
  reading them out.
- Know where data ends up. Audio goes to your STT provider (unless you run
  [local STT](08-stt-and-tts.md)), and transcripts go to your LLM provider.
  On disk, transcripts and replies accumulate in `~/.hermes/state.db` and
  `~/.local/state/baofeng-radio/events.jsonl`, and optional scanner
  recordings in `diagnostics/`. Rotate or delete them on a schedule that
  fits your obligations.

## Access control: anyone on the channel is a user

There is **no authentication** on a radio channel. Anyone with a radio,
your frequency and your tone can command the agent, and anyone with a
recording of you can replay you. That includes prompt injection by voice:
"ignore your instructions and…".

### Default to read-only

Start with tools that only **look things up**. A read-only agent that
gets abused can, at worst, say something it shouldn't, which the data
rules above limit. The [cost trim](07-hermes-cost-trim.md) already removes
the terminal, file and vision toolsets. Keep them off on the radio: an agent
with a shell is a remote shell for everyone in range.

### If you add actions, make them boring to abuse

For anything that changes the world (prints, messages, orders, doors,
devices, money):

| Control | How |
|---|---|
| **Allow-list** | Expose only specific, narrow actions (`print_label(preset, copies)`), never generic ones (`run`, `http_request`, `send_message(to, text)`). |
| **Hard limits in the tool** | Cap arguments in code: copies ≤ 10, amounts ≤ X, recipients from a fixed list. |
| **Rate limits** | Per action: e.g. at most 5 per 10 minutes and 50 per day. When exceeded, refuse with one sentence. See `examples/mcp_stub/server.py`. |
| **Cooldowns and time windows** | Allow risky actions only during working hours, with a pause between them. |
| **Confirmation** | Ask before anything irreversible, and read back exactly what will happen. This guards against **mishearing**, not against impostors. |
| **No irreversible or high-value actions by radio at all** | Deleting, paying, unlocking, sending to arbitrary recipients: do these somewhere with real authentication. |
| **Audit log** | Have the tool, not the model, log every action request (time, arguments, transcript) to a file you review. |
| **Kill switch** | `systemctl --user stop hermes-radio`, or stop `baofeng-tx` to silence it. Know how before you need it. |

Expect the rate limit to fire on the day someone else finds your channel.
That is the point.

### Keep the plumbing private

- The dashboard and scanner API have **no login**. Bind them to a private
  network (a VM bridge or LAN), never to the internet.
- The ESP32 accepts TX packets from the LAN. Anyone on that LAN who can send
  UDP to port 7778 can transmit through your radio. Keep it on a network you
  control, ideally a separate SSID or VLAN.
- Run Hermes in a VM or as a dedicated user, with only the credentials its
  tools need.

## Checklist before going on the air

- [ ] Frequency and power legal for me; not a factory default; listened to first
- [ ] Non-default CTCSS/DCS tone on every radio (understood: not security)
- [ ] Tools are read-only, or every action has an allow-list, hard limits, rate limits and an audit log
- [ ] No tool can return personal data, credentials or codes
- [ ] Terminal/file toolsets disabled on the radio profile
- [ ] Dashboard, scanner API and ESP32 on a private network only
- [ ] Retention plan for `state.db`, `events.jsonl` and recordings
- [ ] Everyone who uses the channel knows it is public
