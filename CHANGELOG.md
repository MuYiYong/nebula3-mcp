# Changelog

## 0.1.0

- First release of the NebulaGraph 3.8 MCP server, ported from the YueShu 5.3 `nebula-mcp` architecture with a NebulaGraph 3.x interface layer: nebula3-python 3.8.3, nGQL, graph spaces, tags and edge types.
- 11 tools: connection test, space discovery and schema (VID type, tags, edges, properties, native indexes, optional DDL), space selection with automatic resume of the blocked query, nGQL validation with optional EXPLAIN, read-only execution, result rendering, confirmed mutations, in-process connection configuration and named environments.
- Read-only policy follows the graphd 3.8 lexer (`#`, `//`, `/* */` comments; `--` is an edge). Composite `$var = ...;` reads and pipes are accepted; any write or administrative keyword is rejected.
- Query results include automatic PROFILE as an operator tree (a leading `USE` is sent separately because PROFILE wraps one sentence), typed values (date, time, datetime, duration, geography WKT, set), vertices keyed by VID with `tag.property` properties, and edges keyed by source, type, rank and destination.
- The MCP App shows nGQL, graph space, VID/Tag and edge identity in the inspector, and 3.x PROFILE metrics. Large VIDs and ranks keep precision in the browser.
- The managed installer, Codex plugin and calendar-named GitHub Release workflow are carried over; the host server name is `nebula3`, so it coexists with the YueShu `nebula` server.

The release targets NebulaGraph 3.8.0 and was verified against a live 3.8.0 cluster with the basketballplayer space. Client support for embedded MCP Apps varies; browser preview validation does not establish rendering support in every client.
