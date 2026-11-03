# Graph Report - Men  (2026-11-03)

## Corpus Check
- Corpus is ~599 words - fits in a single context window. You may not need a graph.

## Summary
- 20 nodes · 18 edges · 6 communities (3 shown, 3 thin omitted)
- Extraction: 83% EXTRACTED · 17% INFERRED · 0% AMBIGUOUS · INFERRED: 3 edges (avg confidence: 0.88)
- Token cost: 65,595 input · 0 output

## Community Hubs (Navigation)
- Graphify Skill & Outputs
- Graph Query Commands
- Graph Data & Updates
- Session Start Install
- AI Studio README
- Hook Guard Wrapper

## God Nodes (most connected - your core abstractions)
1. `Query graph before grep/read rule` - 5 edges
2. `graphify knowledge graph (graphify-out/)` - 4 edges
3. `graphify-out/graph.json` - 4 edges
4. `graphify skill (.claude/skills/graphify/SKILL.md)` - 2 edges
5. `graphify-out/GRAPH_REPORT.md` - 2 edges
6. `Built with AI Studio` - 2 edges
7. `Google AI Studio` - 2 edges
8. `Gemini` - 2 edges
9. `graphify-guard.sh script` - 1 edges
10. `session-start.sh script` - 1 edges

## Surprising Connections (you probably didn't know these)
- `graphify skill (.claude/skills/graphify/SKILL.md)` --conceptually_related_to--> `graphify knowledge graph (graphify-out/)`  [INFERRED]
  .claude/CLAUDE.md → CLAUDE.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **graphify scoped navigation commands** — claude_graphify_query, claude_graphify_path, claude_graphify_explain [EXTRACTED 1.00]

## Communities (6 total, 3 thin omitted)

### Community 0 - "Graphify Skill & Outputs"
Cohesion: 0.40
Nodes (5): graphify skill (.claude/skills/graphify/SKILL.md), /graphify slash command trigger, graphify-out/GRAPH_REPORT.md, graphify knowledge graph (graphify-out/), graphify-out/wiki/index.md

### Community 1 - "Graph Query Commands"
Cohesion: 0.50
Nodes (3): graphify explain, graphify path, graphify query

### Community 4 - "AI Studio README"
Cohesion: 1.00
Nodes (3): Built with AI Studio, Gemini, Google AI Studio

## Knowledge Gaps
- **7 isolated node(s):** `PATH`, `/graphify slash command trigger`, `graphify query`, `graphify path`, `graphify explain` (+2 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 11 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Query graph before grep/read rule` connect `Graph Query Commands` to `Graphify Skill & Outputs`, `Graph Data & Updates`?**
  _High betweenness centrality (0.167) - this node is a cross-community bridge._
- **What connects `PATH`, `/graphify slash command trigger`, `graphify query` to the rest of the system?**
  _7 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Why does `graphify knowledge graph (graphify-out/)` connect `Graphify Skill & Outputs` to `Graph Data & Updates`?**
  _High betweenness centrality (0.161) - this node is a cross-community bridge._
- **Why does `graphify-out/graph.json` connect `Graph Data & Updates` to `Graphify Skill & Outputs`, `Graph Query Commands`?**
  _High betweenness centrality (0.158) - this node is a cross-community bridge._