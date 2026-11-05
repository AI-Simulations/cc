// The inputs of the MCP tools the session had connected when this mod
// was last saved, from each server's tools/list inputSchema.
// Merges into the engine's ToolCallInput (types/ McpToolInputs) so
// `e.tool === "mcp__<server>__<tool>"` narrows to the tool's arguments.
// Written again at a save of the mod with a server connected.
export {}
declare module 'claude-code' {
  interface McpToolInputs {
    /** Create a doc, or apply several operations to one doc atomically. */
    mcp__claude_ai_Claude_Docs__batch: {
      batch?: unknown[]
      container?: {
        kind: string
        id?: string
        create?: {}
      }
      verbose?: boolean
      opId?: string
    }
    /** Create one object in a doc: a tab, its contents, a comment, an upload record. */
    mcp__claude_ai_Claude_Docs__create: {
      object: "file" | "node" | "utterance" | "enum" | "blob"
      engine?: string
      payload: {} | string
      container?: {
        kind: string
        id: string
        version?: string
      }
      verbose?: boolean
      opId?: string
      artifact?: string
    }
    /** Delete one object from a doc: a tab or a comment. A doc keeps at least one tab (deleting its last refuses `last_tab`): to start over, rewrite that tab's contents with `update`, never delete and recreate the tab. */
    mcp__claude_ai_Claude_Docs__delete: {
      ref: {
        object: "project" | "file" | "node" | "utterance"
        id: string
      }
      engine?: string
      container?: {
        kind: string
        id: string
        version?: string
      }
      payload?: {} | string
      verbose?: boolean
      opId?: string
    }
    /** Export one tab inline as base64: pdf, docx, html, text, markdown or notion (Notion-flavored markdown, what notion-create-pages takes). To just keep the file in the doc's files, create a blob {from: {object: "file", id}, format} instead (no large result). */
    mcp__claude_ai_Claude_Docs__export: {
      container: {
        kind: string
        id: string
        version?: string
      }
      file: string
      format: "markdown" | "text" | "html" | "docx" | "pdf" | "notion"
      paper?: "letter" | "a4"
      maxBytes?: number
      /** "fit": send it only when a refusal offers it or a docx's notes say `(file too large to deliver)`, and only where you have a shell or file tools (your client then saves the large result as a file). The file may keep the pictures that fit. `data.notes` counts those left out: tell the user how many. Those counts come first, in the result's first 2,000 characters. */
      pictures?: "fit"
    }
    /** Docs guides: topic.instructions repeats the server instructions. Read it only if your client dropped them. Also topic.<name>, refusal.<code>. After a doc's birth → ["topic.index"]. */
    mcp__claude_ai_Claude_Docs__guide: {
      /** topic.<name> (instructions, index, editing, tabs, comments, charts, chart-definition, diagram, uploads, sharing, skill) or refusal.<code>; several per call is fine. */
      items?: unknown[]
    }
    /** List a tab's or a doc's comment history (threads, replies, resolves). */
    mcp__claude_ai_Claude_Docs__query: {
      container?: {
        kind: string
        id: string
        version?: string
      }
      object?: "utterance"
      payload?: {} | string
    }
    /** Read a doc (lists its tabs), a tab's contents, or a comment. A claude.ai/[code/]artifact/[<title>-]<id> link → `ref {"object":"project","id":"<id>"}` first; reads inside it take `container {"kind":"project","id":"<id>"}`. */
    mcp__claude_ai_Claude_Docs__read: {
      ref: {
        object: "project" | "file" | "node" | "utterance" | "enum" | "blob"
        id: string
      }
      engine?: string
      container?: {
        kind: string
        id: string
        version?: string
      }
      payload?: {} | string
    }
    /** Edit a tab's contents, rename a doc or tab, or change a stored value. */
    mcp__claude_ai_Claude_Docs__update: {
      ref: {
        object: "project" | "file" | "node" | "utterance" | "enum"
        id: string
      }
      engine?: string
      payload: {} | string
      container?: {
        kind: string
        id: string
        version?: string
      }
      verbose?: boolean
      opId?: string
      answering?: string
    }
    /** PRIMARY TOOL — call FIRST for almost any question OR before an edit: how does X work, architecture, a bug, where/what is X, surveying an area, or the symbols you are about to change. Returns the verbatim source of the relevant symbols grouped by file in ONE capped call (Read-equivalent — treat the shown source as already Read; do NOT re-open those files), plus the call path among them. Query can be a natural-language question OR a bag of symbol/file names. Usually the ONLY call you need — more accurate context, in far fewer tokens and round-trips than a search/Read/Grep loop. */
    mcp__codegraph__codegraph_explore: {
      /** Symbol names, file names, or short code terms to explore (e.g., "AuthService loginUser session-manager", "GraphTraverser BFS impact traversal.ts"). For a flow question, name the symbols spanning the flow (e.g. "mutateElement renderScene"). A natural-language question works too — no prior codegraph_search needed. */
      query: string
      /** Maximum number of files to include source code from (default: 12) */
      maxFiles?: number
      /** Absolute path to the project to query (or any directory inside it) — codegraph uses the nearest .codegraph/ index at or above that path. Omit to use this session's default project. Pass it to query a second codebase, or when the server root has no index of its own (e.g. a monorepo where only sub-projects are indexed, so there is no default project). */
      projectPath?: string
    }
  }
}
