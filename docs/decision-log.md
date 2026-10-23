> **Superseded by [`decisions.md`](decisions.md); kept for history.**

# Project Context — Multi-Team Delivery Analytics Pipeline

This document exists so that any future session (Claude Code, a Claude Project, or a human teammate) can pick up this project without re-deriving decisions that were already made and stress-tested. Read this before proposing alternative approaches to anything listed under "Decisions" — those were reached by deliberately arguing against the easier options first.

## 1. Who this is for and why it exists

Yassine is a second-year (M2) master's student targeting a junior data/BI engineer role, currently on an internship with no access to company data. This project was proposed as a self-directed internship deliverable: a portfolio piece meant to demonstrate real engineering + BI judgment, not a tutorial clone. It needs to take at least ~2-3 weeks and needs to hold up under technical interview scrutiny — every design choice should have a defensible "why," including the choices that were rejected.

## 2. The problem this project solves

Jira already provides native reporting: velocity chart, sprint report, burndown/burnup, cumulative flow diagram. For a single team on a single board, these are sufficient — do not reproduce them 1:1, that is not a project, it's a worse copy of something free.

The actual gap that justifies this project existing:

- Native Jira reports are scoped to one project/board at a time. No native cross-team/cross-project portfolio view without paid add-ons (Jira Align, Premium tiers).
- Jira has no concept of anything outside itself — specifically, no notion of team capacity (headcount, absences). Two teams with identical raw velocity are not equivalent if one had a full roster and the other lost half its capacity to leave/absence that sprint.

The project's reason to exist: build the layer that blends Jira delivery data with team capacity data across multiple teams/projects — something native Jira cannot do without paid extensions.

## 3. Confirmed business questions (v1 scope)

These are deliberately restricted to questions directly computable from the planned data, with no causal inference layered on top. This list was trimmed down from a larger set that included things like "why did velocity decline" — those were explicitly excluded because Jira-shaped data can show correlation/pattern but can't support causal claims without inventing context that isn't really there.

1. How does each team's velocity evolve sprint over sprint?
2. What is a team's velocity relative to its actual available capacity that sprint (not theoretical headcount)?
3. What is the SLA/deadline breach rate, broken down by issue type (bug, story, task)?
4. Where do tickets get stuck the longest (time spent per status)?
5. What is the bug-to-story ratio, and how does it trend over time?
6. How do all of the above compare across teams/projects (portfolio view)?

Explicitly out of scope for v1: root-cause/"why" questions, business value/ROI of shipped work, anything requiring a data source not listed below.

## 4. Data sources

Two sources, both synthetically generated — there is no access to real company or real Jira data for this project.

- Simulated Jira data: multiple teams/projects, issues, full changelog (status transition history with timestamps — this is the critical field; without it, none of the time-in-status or cycle-time questions are answerable, you'd only have current-state snapshots), and sprints (commit vs. completion, scope added mid-sprint).
- Simulated capacity data: headcount and absence/PTO days per team per sprint. This is the source that justifies the project's existence — it's what Jira itself cannot provide, and what makes the "velocity relative to capacity" question possible.

## 5. Decision log — approaches considered and rejected

This is the most important section to preserve, because the reasoning is easy to lose and easy to accidentally re-argue from scratch in a future session.

### 5.1 Why not pull data from a real public Jira instance (Apache, Red Hat, etc.)?

Public instances (issues.apache.org/jira, issues.redhat.com, etc.) do allow anonymous REST reads and were considered. Rejected as the primary data source because:

- They are open-source bug trackers, not Scrum/Kanban team environments — no sprint data, no stable team/assignee structure, so the "team performance" framing the project is built around doesn't hold.
- Using them would produce a different project ("OSS issue triage analytics") than the one actually scoped.
- They remain a fine source of inspiration for realistic field values/issue phrasing, just not as the dataset itself.

### 5.2 Why not hand-write/ask an LLM to generate the dataset as CSV directly?

Considered and rejected for several concrete reasons, not just "best practice":

- Scale: the dataset needs thousands of interrelated rows across issues/changelog/sprints/capacity — unreliable to produce accurately in one-shot text generation.
- Referential integrity: changelog entries must reference real issue IDs, be chronologically ordered, fall within each issue's actual lifecycle, and sprint completion totals must equal the sum of issues marked done — these are arithmetic/referential constraints that code enforces trivially and free-text generation does not reliably satisfy.
- Statistical realism: LLM-generated numeric data tends toward unnatural patterns (overused round numbers, repeated clustering) rather than genuine distributions. A numpy sample from a log-normal distribution is more statistically honest than hand-typed "random-looking" numbers.
- Reproducibility: a script can be re-run with one parameter changed (e.g., team count); a one-off LLM-generated CSV cannot be regenerated consistently.
- LLM generation remains useful for small illustrative samples (10-20 rows) to validate an intended schema, or for flavor text (issue titles/summaries) — not as the dataset of record.

### 5.3 Why not feed the generated data into a real Jira Cloud instance and extract it via the API?

This was seriously considered because it would feel more "authentic," and was rejected for a specific technical reason, not a stylistic one:

- Jira's REST API stamps created/updated/changelog timestamps at the moment the API call is made. There is no supported way to backdate a status transition (e.g., to "3 months ago") through the normal API.
- Getting genuinely realistic multi-month history this way would require either spending the real wall-clock time to do it (incompatible with a 2-3 week project) or manipulating a hosted service's clock (fragile, likely against ToS).
- Since backdated, controllable history is a hard requirement (the cycle-time/bottleneck questions depend on it), this option was dropped in favor of full control via simulation.

### 5.4 Why simulation instead of naive per-field randomness?

Naive Python (random.choice() per field, independently) was flagged early as producing "random-looking but structurally meaningless" data — no correlation between e.g. a capacity drop and a velocity drop, unrealistic flat distributions instead of the log-normal-ish shape real cycle times actually have.

Two better approaches were identified:

- **Option A — Discrete-event simulation (e.g. SimPy):** model the actual process (ticket arrivals, resource-constrained pickup, queueing, blocking) so that realistic correlations emerge from the simulation mechanics rather than being hand-scripted. More technically ambitious, most realistic result.
- **Option B — Statistical generation (numpy/scipy) + explicitly scripted events:** sample fields (cycle time, story points) from realistic distributions, then deliberately inject a small number of named events (a scope-creep sprint, a capacity-drop sprint) on top. Simpler to build and reason about within the timeline; the causal structure is manually authored rather than emergent, and that should be stated plainly if asked rather than implied to be organic.

Decision was left open between A and B — leaning toward B for timeline reasons, but not finalized as of this document. Whichever is picked, the data must be served through a mock layer, not consumed directly as flat files — see 5.5.

**Update: Decided on Option A (SimPy).**

### 5.5 Why serve the simulated data through a mock API instead of reading it directly from local files?

Decided: the simulation output should be serialized into JSON matching Jira's actual REST API response shapes (/rest/api/2/search, /rest/api/2/issue/{key}/changelog, sprint report format — to be pulled from Jira's public API docs) and served via a small FastAPI/Flask app that mimics those endpoints, including pagination (startAt/maxResults).

Rationale: the ingestion layer should be built as a genuine API client (auth headers, pagination handling, JSON parsing against a real schema) — that is the actual engineering skill being demonstrated, not "read a local CSV." It also means the same client could point at real Jira later with minimal changes, since it was built against the real contract.

Honesty note for interviews: if asked whether this used live Jira, the accurate answer is "built against a mock replicating Jira's real API contract, because the live API doesn't allow backdating historical timestamps, which the project needed." This is a defensible, engineering-literate answer — do not blur it into implying live Jira was used.

## 6. Data model (direction, not finalized)

Star schema, exact columns/grain to be finalized once the generator's output structure is concrete. Planned shape:

- Fact table: ticket status-transition events (one row per status change, with timestamp) — this is the grain that makes cycle-time and time-in-status calculations possible.
- Dimensions: ticket, sprint, team/project, user.
- Capacity table: headcount/absence, keyed by team + sprint.

## 7. v1 goal

Not a complete/final product. A solid, extensible base: working pipeline (mock API → ingestion → star schema), a