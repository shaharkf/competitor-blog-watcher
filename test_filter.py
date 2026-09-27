#!/usr/bin/env python3
"""
Regression corpus for should_alert(), built from real posts that appeared in
#competitor-blogpost-alert (13-27 Sep 2026) plus the tier-1 sources added in
the September revision.

Run:  python3 test_filter.py
"""
from watcher import clean_title, should_alert

# (source, tier, raw_title, summary, expected_keep)
CASES = [
    # ---- marketing noise that reached the channel and should not have ----
    ("Obsidian Security", 3,
     "Product SpotlightsObsidian Security Named a Databricks Silver Partner — One of "
     "Only Two Security Vendors at Silver or AboveObsidian Security today announced it "
     "has achieved Silver-tier status in the new Databricks Brickbuilder Partner "
     "Network, launched September 1, 2026.September 15, 20265 minRead more", "", False),
    ("Lasso Security", 1, "Lasso Named a Leader in Latio's 2026 AI Security Market Report", "", False),
    ("Lasso Security", 1, "Lasso Named in Gartner's Market Overview for AI Usage Control", "", False),
    ("Lasso Security", 1, "Lasso Named in Gartner's Hype Cycle for Artificial Intelligence, 2026", "", False),
    ("CodeIntegrity", 1, "CodeIntegrity raises $5M in seed funding", "", False),
    ("Mindgard", 2, "PRESS RELEASE: Mindgard Expands AI and Cloud Ecosystem with Anthropic, "
                    "NVIDIA, Microsoft, Google Cloud and AWS", "", False),
    ("Varonis", 3, "Introducing Varonis Data Lifecycle Management",
     "Varonis Data Lifecycle Management (DLM) automatically finds and quarantines "
     "redundant, obsolete and trivial data. Cut storage costs, improve AI outputs.", False),

    # ---- big-corp feeds that are not about AI at all ----
    ("Wiz (Google)", 3, "Investing Together: Wiz Defend and Google Security Operations", "", False),
    ("Cisco AI Defense", 3, "The Honest Migration Playbook – IPsec Series, Part 11",
     "A fully quantum-safe tunnel on Cisco 8000 routers, both pillars live on real hardware.", False),
    ("Cisco AI Defense", 3, "Closing the Resilience Gap with Native Splunk in Cisco Nexus One",
     "See how Native Splunk and Cisco Nexus One bring analytics closer to network data.", False),
    ("Cisco AI Defense", 3, "Cisco and the DISA STIG: Turning Zero Trust Policy into "
                            "Repeatable Practice – Part 2: Cisco SNA",
     "Discover how the new DISA STIG for Cisco Secure Network Analytics helps defense "
     "organizations securely configure and harden their analytics platform.", False),
    ("Cisco AI Defense", 3, "Building for the AI era: Inside Cisco IT's Wi-Fi 7 revolution",
     "Cisco IT upgraded to Wi-Fi 7 for 90,000 employees with zero downtime.", False),
    ("Cisco AI Defense", 3, "Rail Network Modernization: FRMCS, Sovereignty and the AI Edge",
     "Join Cisco at InnoTrans 2026 to explore the next generation of rail networks.", False),
    ("LatticeFlow AI", 2, "READ ARTICLE", "", False),

    # ---- real signal that must keep getting through ----
    ("Backslash", 1, "When AI Agents Own The Endpoint: The Security Gap That No One is Watching", "", True),
    ("Backslash", 1, "AI Workspace Impersonation: Exploiting Trust at the Tenant Boundary", "", True),
    ("Lasso Security", 1, "MaxKBypass - From Prompt Injection To Bypassing MaxKB Agent's "
                          "Sandbox (CVE-2026-77521)", "", True),
    ("CodeIntegrity", 1, "Claude Code Cross-Session Escalation Risks", "", True),
    ("CodeIntegrity", 1, "The Hidden Risk in Notion 3.0 AI Agents: Web Search Tool Abuse "
                         "for Data Exfiltration", "", True),
    ("Zenity", 1, "Introducing Guardian Agents: Meet Blue Agent, Your AI Security Analyst", "", True),
    ("Lasso Security", 1, "Introducing LEAP: CPU-based AI Security Guardrails with GPU-Class Accuracy", "", True),
    ("Zenity", 1, "Seeing Every MCP Connection: Zenity Joins the Cursor Marketplace", "", True),

    # ---- newly added tier-1 sources ----
    ("AIR Security", 1, "Anthropic built a skill scanner, so we tested it", "", True),
    ("Pluto Security", 1, "Endpoint Visibility for AI Agents: What Your EDR Isn't Logging", "", True),
    ("Pluto Security", 1, "Inside Claude Cowork: How Anthropic's Autonomous Agent Actually Works", "", True),
    ("Pluto Security", 1, "AI Agent Authorization: Least Privilege Fails at the Endpoint", "", True),
    ("Helmet Security", 1, "The MCP Debrief - What's Actually Running on Your Endpoints", "", True),
    ("Glow", 1, "Why We Started Glow", "", False),  # company-story marketing

    # ---- tier 2/3 still get through on genuine research ----
    ("NeuralTrust", 2, "Prompt injection in MCP gateways: bypassing tool-call allowlists (CVE-2026-11002)", "", True),
    ("Wiz (Google)", 3, "Attacking MCP servers: a new class of supply-chain vulnerability", "", True),
    ("Astrix Security", 2, "How we found agent tokens leaking through OAuth refresh flows", "", True),
    # ...but not on feature launches
    ("Harmonic Security", 2, "Announcing Harmonic for Microsoft Copilot", "", False),
]


def main() -> int:
    fails = []
    for source, tier, raw, summary, expected in CASES:
        entry = {"title": clean_title(raw), "summary": summary, "link": ""}
        keep, reason = should_alert(entry, tier)
        status = "ok  " if keep == expected else "FAIL"
        if keep != expected:
            fails.append((source, raw[:60], expected, keep, reason))
        verdict = "KEEP" if keep else "drop"
        print(f"{status} [{verdict}:{reason:<18}] t{tier} {source}: {entry['title'][:78]}")

    print()
    if fails:
        print(f"{len(fails)}/{len(CASES)} FAILED:")
        for s, t, exp, got, why in fails:
            print(f"  {s}: {t!r} expected {'KEEP' if exp else 'drop'}, got {'KEEP' if got else 'drop'} ({why})")
        return 1
    print(f"all {len(CASES)} cases pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
