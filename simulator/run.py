"""Entry point for orchestrating the agile analytics simulation."""

from __future__ import annotations


def main() -> None:
    """Orchestrate the full simulation run.

    This is a stub for the eventual simulation pipeline. Once implemented,
    this function is intended to, per simulator/domain-model.md:

    1. Create the set of Team and User instances that participate in the
       simulation, along with each team's base_headcount.
    2. Run the simulation across a series of Sprints, generating
       per-team, per-sprint Capacity records (headcount and pto_days).
    3. Generate Issues owned by teams, including their issue_type,
       story_points (not applicable to Epics), and parent_id links
       (Epic->Story, Story->Task/Bug).
    4. Generate SprintIssueMembership records marking when each issue
       joined a sprint (added_at), and StatusTransition history for each
       issue, reflecting how it moved through workflow statuses over
       time. Together these are what allow committed/completed/
       scope-added points to be computed downstream, the same way Jira's
       own Sprint Report does.

    No simulation logic (e.g. SimPy processes/events) is implemented yet;
    this is purely a placeholder for that future orchestration.
    """
    pass


if __name__ == "__main__":
    main()
