"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import rehypeSanitize from "rehype-sanitize";
import { ArrowLeft, ArrowUp, ChevronDown, Menu, MessageSquareText, MoreHorizontal, Pencil, Plus, Quote, Sparkles, X } from "lucide-react";
import { postSSE } from "@/lib/sse";
import { useUIStore, type TextSelection } from "@/stores/ui";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
type Message = { id: string; seq: number; role: "user" | "assistant"; content: string; status: string };
type Anchor = { text: string; start: number; end: number; prefix: string; suffix: string };
type Thread = { id: string; parent_message_id: string; anchor: Anchor; message_count: number };
type Conversation = { id: string; title: string; updated_at?: string };
type DeviceUser = { id: string; role: string; is_synthetic: boolean };
type HNode = { type: string; value?: string; tagName?: string; properties?: Record<string, unknown>; children?: HNode[] };
let deviceLoginPromise: Promise<DeviceUser> | null = null;

async function api(path: string, init?: RequestInit) {
  const response = await fetch(`${API}${path}`, { credentials: "include", ...init });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}

function rehypeAnchors(threads: Thread[]) {
  return (tree: HNode) => {
    const textNodes: HNode[] = [];
    const collect = (node: HNode) => { if (node.type === "text") textNodes.push(node); node.children?.forEach(collect); };
    tree.children?.forEach(collect);
    const text = textNodes.map((node) => node.value ?? "").join("");
    const ranges = threads.flatMap((thread) => {
      let start = thread.anchor.start;
      if (text.slice(start, start + thread.anchor.text.length) !== thread.anchor.text) {
        const contextual = thread.anchor.prefix + thread.anchor.text + thread.anchor.suffix;
        const match = contextual.length > thread.anchor.text.length ? text.indexOf(contextual) : -1;
        start = match >= 0 ? match + thread.anchor.prefix.length : text.indexOf(thread.anchor.text);
      }
      return start < 0 ? [] : [{ id: thread.id, start, end: start + thread.anchor.text.length }];
    });
    let offset = 0;
    const rewrite = (node: HNode): HNode[] => {
      if (node.type === "text") {
        const value = node.value ?? "";
        const nodeStart = offset;
        offset += value.length;
        const cuts = new Set([0, value.length]);
        for (const range of ranges) {
          if (range.start > nodeStart && range.start < nodeStart + value.length) cuts.add(range.start - nodeStart);
          if (range.end > nodeStart && range.end < nodeStart + value.length) cuts.add(range.end - nodeStart);
        }
        const points = [...cuts].sort((a, b) => a - b);
        return points.slice(0, -1).map((from, index) => {
          const to = points[index + 1];
          const ids = ranges.filter((range) => range.start < nodeStart + to && range.end > nodeStart + from).map((range) => range.id);
          const child: HNode = { type: "text", value: value.slice(from, to) };
          return ids.length ? { type: "element", tagName: "span", properties: { className: ["anchor"], dataThreadIds: ids.join(" ") }, children: [child] } : child;
        });
      }
      if (node.children) node.children = node.children.flatMap(rewrite);
      return [node];
    };
    if (ranges.length) tree.children = tree.children?.flatMap(rewrite);
  };
}

function Markdown({ content, threads, onAnchor }: { content: string; threads: Thread[]; onAnchor: (ids: string[]) => void }) {
  return <div className="markdown-body" data-message-body><ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight, rehypeSanitize, [rehypeAnchors, threads] as never]} components={{
    span: ({ node: _node, className, ...props }) => {
      void _node;
      const ids = (props as { "data-thread-ids"?: string })["data-thread-ids"];
      return className?.split(" ").includes("anchor")
        ? <span {...props} className={`${className} anchor`} data-thread-ids={ids} role="button" tabIndex={0} aria-label="Open AskPanel thread" onClick={() => ids && onAnchor(ids.split(" "))} onKeyDown={(event) => { if ((event.key === "Enter" || event.key === " ") && ids) onAnchor(ids.split(" ")); }} />
        : <span {...props} className={className} />;
    },
  }}>{content}</ReactMarkdown></div>;
}

function deviceId() {
  const storageKey = "askpanel-device-id";
  let id = localStorage.getItem(storageKey);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(storageKey, id);
  }
  return id;
}

function loginDevice(): Promise<DeviceUser> {
  if (!deviceLoginPromise) {
    deviceLoginPromise = api("/api/auth/device", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: deviceId() }),
    }).catch((error) => {
      deviceLoginPromise = null;
      throw error;
    });
  }
  return deviceLoginPromise;
}

export default function ChatWorkspace() {
  const [user, setUser] = useState<DeviceUser | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [popover, setPopover] = useState<{ x: number; y: number } | null>(null);
  const [threadOpen, setThreadOpen] = useState(false);
  const [activeThread, setActiveThread] = useState<Thread | null>(null);
  const [threadMessages, setThreadMessages] = useState<Message[]>([]);
  const [threadDraft, setThreadDraft] = useState("");
  const [threadBusy, setThreadBusy] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [chooser, setChooser] = useState<Thread[]>([]);
  const [mobileSidebar, setMobileSidebar] = useState(false);
  const mainScroller = useRef<HTMLDivElement>(null);
  const threadScroller = useRef<HTMLDivElement>(null);
  const threadFollowsBottom = useRef(true);
  const { sidebarOpen, selection, setSelection, toast, setToast, toggleSidebar } = useUIStore();

  const log = useCallback((type: string, payload: Record<string, unknown> = {}, threadId?: string) => {
    if (!user) return;
    const next = { type, payload, conversation_id: conversationId || undefined, thread_id: threadId, client_ts: new Date().toISOString() };
    const events = JSON.parse(sessionStorage.getItem("askpanel-events") ?? "[]") as Record<string, unknown>[];
    events.push(next);
    sessionStorage.setItem("askpanel-events", JSON.stringify(events));
  }, [conversationId, user]);

  const flushEvents = useCallback(() => {
    const stored = sessionStorage.getItem("askpanel-events");
    if (!stored) return;
    const events = JSON.parse(stored) as Record<string, unknown>[];
    sessionStorage.removeItem("askpanel-events");
    void api("/api/events", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ events }) })
      .catch(() => sessionStorage.setItem("askpanel-events", JSON.stringify(events)));
  }, []);

  const loadConversation = useCallback(async (id: string) => {
    const data = await api(`/api/conversations/${id}`);
    setConversationId(data.id);
    setMessages(data.messages);
    setThreads(data.threads ?? []);
    setSelection(null);
  }, [setSelection]);

  const loadHistory = useCallback(async () => {
    const rows = await api("/api/conversations");
    setConversations(rows);
    if (rows.length) await loadConversation(rows[0].id);
  }, [loadConversation]);

  useEffect(() => {
    let cancelled = false;
    async function initializeDevice() {
      try {
        const current = await loginDevice();
        if (cancelled) return;
        setUser(current);
        await loadHistory();
      } catch (error) {
        if (!cancelled) setToast(error instanceof Error ? error.message : "Could not initialize this device");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void initializeDevice();
    return () => { cancelled = true; };
  }, [loadHistory, setToast]);

  useEffect(() => {
    const interval = window.setInterval(flushEvents, 5000);
    const onHide = () => {
      const stored = sessionStorage.getItem("askpanel-events");
      if (stored) navigator.sendBeacon(`${API}/api/events`, new Blob([JSON.stringify({ events: JSON.parse(stored) })], { type: "application/json" }));
      sessionStorage.removeItem("askpanel-events");
    };
    const onVisibility = () => { if (document.visibilityState === "hidden") onHide(); };
    window.addEventListener("pagehide", onHide);
    document.addEventListener("visibilitychange", onVisibility);
    return () => { window.clearInterval(interval); window.removeEventListener("pagehide", onHide); document.removeEventListener("visibilitychange", onVisibility); };
  }, [flushEvents]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 3000);
    return () => window.clearTimeout(timer);
  }, [toast, setToast]);

  useEffect(() => {
    const onSelection = () => {
      window.clearTimeout((onSelection as { timer?: number }).timer);
      (onSelection as { timer?: number }).timer = window.setTimeout(() => {
        if (busy) return;
        const current = window.getSelection();
        const text = current?.toString() ?? "";
        if (!current || current.isCollapsed || !text.trim() || text.length > 4000) return;
        const bodyFor = (node: Node | null) => node instanceof Element ? node.closest("[data-message-body]") : node?.parentElement?.closest("[data-message-body]");
        const body = bodyFor(current.anchorNode);
        if (!body || body !== bodyFor(current.focusNode)) return;
        const article = body.closest<HTMLElement>("[data-message-id]");
        if (!article || article.dataset.status === "streaming") return;
        const range = current.getRangeAt(0);
        const before = range.cloneRange();
        before.selectNodeContents(body);
        before.setEnd(range.startContainer, range.startOffset);
        const start = before.toString().length;
        const fullText = body.textContent ?? "";
        const rect = range.getBoundingClientRect();
        const picked: TextSelection = {
          messageId: article.dataset.messageId ?? "", text, start, end: start + text.length,
          prefix: fullText.slice(Math.max(0, start - 40), start),
          suffix: fullText.slice(start + text.length, start + text.length + 40),
          rect: { top: rect.top, left: rect.left, right: rect.right, bottom: rect.bottom },
        };
        setSelection(picked);
        setPopover({ x: Math.max(12, Math.min(rect.left, window.innerWidth - 180)), y: Math.max(12, rect.top - 48) });
        log("selection_made", { len: text.length, message_id: picked.messageId, in_code: Boolean(body.closest("pre")) });
        log("selection_popover_shown");
      }, 150);
    };
    document.addEventListener("selectionchange", onSelection);
    document.addEventListener("mouseup", onSelection);
    document.addEventListener("touchend", onSelection);
    return () => {
      document.removeEventListener("selectionchange", onSelection);
      document.removeEventListener("mouseup", onSelection);
      document.removeEventListener("touchend", onSelection);
    };
  }, [busy, log, setSelection]);

  useEffect(() => {
    if (mainScroller.current && !mainScroller.current.dataset.userScrolled) mainScroller.current.scrollTop = mainScroller.current.scrollHeight;
  }, [messages]);
  useEffect(() => {
    const scroller = threadScroller.current;
    if (scroller && threadFollowsBottom.current) scroller.scrollTop = scroller.scrollHeight;
  }, [threadMessages]);
  useEffect(() => {
    if (!threadOpen) return;
    threadFollowsBottom.current = true;
    const frame = window.requestAnimationFrame(() => {
      const scroller = threadScroller.current;
      if (scroller) scroller.scrollTop = scroller.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeThread?.id, threadOpen]);
  useEffect(() => {
    if (!threadOpen || !activeThread?.id) return;
    const refresh = async () => {
      try {
        const data = await api(`/api/threads/${activeThread.id}`);
        setThreadMessages(data.messages);
      } catch { /* The thread may be temporarily unavailable while being created. */ }
    };
    const interval = window.setInterval(() => { void refresh(); }, 1200);
    return () => window.clearInterval(interval);
  }, [activeThread?.id, threadOpen]);

  async function createConversation() {
    try {
      const created = await api("/api/conversations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "New chat" }),
      });
      setConversations((items) => [created, ...items]);
      setMessages([]);
      setThreads([]);
      setConversationId(created.id);
      setSelection(null);
      setMobileSidebar(false);
    } catch (error) { setToast(error instanceof Error ? error.message : "Could not create chat"); }
  }

  async function streamMain(path: string, payload: unknown, userMessage: Message) {
    const tempId = `assistant-${Date.now()}`;
    setMessages((items) => {
      const retained = items.filter((item) => item.seq <= userMessage.seq);
      const existing = retained.findIndex((item) => item.id === userMessage.id);
      if (existing >= 0) retained[existing] = userMessage;
      else retained.push(userMessage);
      return [...retained, { id: tempId, seq: userMessage.seq + 1, role: "assistant", content: "", status: "streaming" }];
    });
    setBusy(true);
    try {
      await postSSE(`${API}${path}`, payload, (event) => {
        if (event.event === "meta") setMessages((items) => items.map((item) => item.id === tempId ? { ...item, id: String(event.data.assistant_message_id) } : item));
        if (event.event === "delta") setMessages((items) => items.map((item) => item.status === "streaming" && item.role === "assistant" ? { ...item, content: item.content + String(event.data.text ?? "") } : item));
        if (event.event === "done") setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, status: "complete" } : item));
        if (event.event === "error") throw new Error(String(event.data.message ?? "Generation failed"));
      });
      log("stream_end");
      void loadHistory();
    } catch (error) {
      setToast(error instanceof Error ? error.message : "Could not send message");
      setMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, status: "error" } : item));
    } finally { setBusy(false); }
  }

  async function sendMain(event?: FormEvent) {
    event?.preventDefault();
    const content = draft.trim();
    if (!content || busy || !conversationId) return;
    const message: Message = { id: `user-${Date.now()}`, seq: (messages.at(-1)?.seq ?? 0) + 1, role: "user", content, status: "complete" };
    setDraft("");
    log("message_send", { len: content.length });
    log("stream_start");
    if (messages.length === 0) {
      const title = content.slice(0, 48) || "New chat";
      void api(`/api/conversations/${conversationId}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }),
      }).then(() => setConversations((items) => items.map((item) => item.id === conversationId ? { ...item, title } : item))).catch(() => undefined);
    }
    await streamMain(`/api/conversations/${conversationId}/messages`, { content }, message);
  }

  async function openThread(thread?: Thread) {
    if (!thread && !selection) return;
    if (thread) {
      setActiveThread(thread);
      try {
        const data = await api(`/api/threads/${thread.id}`);
        setThreadMessages(data.messages);
      } catch { setThreadMessages([]); }
    } else {
      setActiveThread({
        id: "", parent_message_id: selection!.messageId,
        anchor: { text: selection!.text, start: selection!.start, end: selection!.end, prefix: selection!.prefix, suffix: selection!.suffix },
        message_count: 0,
      });
      setThreadMessages([]);
    }
    setPopover(null);
    setChooser([]);
    setThreadOpen(true);
    log("modal_open", { thread_id: thread?.id ?? "new", via: thread ? "anchor" : "popover" }, thread?.id);
  }

  function clickAnchor(ids: string[]) {
    const found = ids.map((id) => threads.find((thread) => thread.id === id)).filter((thread): thread is Thread => Boolean(thread));
    if (found.length === 1) void openThread(found[0]);
    else if (found.length > 1) setChooser(found.sort((a, b) => (a.anchor.end - a.anchor.start) - (b.anchor.end - b.anchor.start)));
  }

  async function sendThread(event: FormEvent) {
    event.preventDefault();
    if (!activeThread || !threadDraft.trim() || threadBusy) return;
    const content = threadDraft.trim();
    const first = !activeThread.id;
    const tempId = `thread-assistant-${Date.now()}`;
    const userMessage: Message = { id: `thread-user-${Date.now()}`, seq: (threadMessages.at(-1)?.seq ?? 0) + 1, role: "user", content, status: "complete" };
    setThreadDraft("");
    setThreadMessages((items) => [...items, userMessage, { id: tempId, seq: userMessage.seq + 1, role: "assistant", content: "", status: "streaming" }]);
    setThreadBusy(true);
    log(first ? "thread_create" : "thread_message_send", { len: content }, activeThread.id || undefined);
    try {
      const path = first ? "/api/threads" : `/api/threads/${activeThread.id}/messages`;
      const payload = first ? { message_id: activeThread.parent_message_id, anchor: activeThread.anchor, content } : { content };
      await postSSE(`${API}${path}`, payload, (event) => {
        if (event.event === "thread_created") {
          const id = String(event.data.thread_id);
          setActiveThread((value) => value ? { ...value, id, message_count: 2 } : value);
          setThreads((items) => [...items, { ...activeThread, id, message_count: 2 }]);
          void loadHistory();
        }
        if (event.event === "meta") setThreadMessages((items) => items.map((item) => item.id === tempId ? { ...item, id: String(event.data.assistant_message_id) } : item));
        if (event.event === "delta") setThreadMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, content: item.content + String(event.data.text ?? "") } : item));
        if (event.event === "done") setThreadMessages((items) => items.map((item) => item.status === "streaming" ? { ...item, status: "complete" } : item));
        if (event.event === "error") throw new Error(String(event.data.message ?? "Thread generation failed"));
      });
    } catch (error) { setToast(error instanceof Error ? error.message : "AskPanel reply failed"); }
    finally { setThreadBusy(false); }
  }

  async function saveEdit(message: Message) {
    const later = messages.filter((item) => item.seq > message.seq);
    const removedThreads = threads.filter((thread) => later.some((item) => item.id === thread.parent_message_id));
    if (later.length && !window.confirm(`This removes ${later.length} later main-chat messages and ${removedThreads.length} anchored AskPanel chats.`)) {
      log("edit_confirm_cancelled", { seq: message.seq }); setEditing(null); return;
    }
    log("message_edit", { seq: message.seq, removed_messages: later.length, removed_threads: removedThreads.length });
    setThreads((items) => items.filter((thread) => !removedThreads.some((removed) => removed.id === thread.id)));
    setEditing(null);
    await streamMain(`/api/messages/${message.id}`, { content: editDraft }, { ...message, content: editDraft });
  }

  function askFromButton() {
    if (!selection) { setToast("Select text in an assistant reply first"); log("ask_click_no_selection"); return; }
    log("ask_click_with_selection");
    void openThread();
  }

  if (loading) return <div className="loading-screen"><span className="brand-mark"><MessageSquareText size={18} /></span>AskPanel</div>;

  return <main className="app-shell">
    <aside className={`sidebar ${sidebarOpen ? "open" : "closed"} ${mobileSidebar ? "mobile-open" : ""}`}>
      <div className="sidebar-head"><div className="brand-lockup"><span className="brand-mark"><MessageSquareText size={16} /></span><span>AskPanel<small>PRIVATE CHAT</small></span></div><button className="icon-button sidebar-collapse" aria-label="Collapse sidebar" onClick={toggleSidebar}><ArrowLeft size={17} /></button><button className="icon-button mobile-close" aria-label="Close sidebar" onClick={() => setMobileSidebar(false)}><X size={18} /></button></div>
      <button className="new-chat-button" onClick={() => void createConversation()}><Plus size={16} /> New chat</button>
      <div className="sidebar-section"><span>CHAT HISTORY</span><button aria-label="Refresh chat history" onClick={() => void loadHistory()}><MoreHorizontal size={17} /></button></div>
      <nav className="conversation-list">{conversations.map((conversation) => <button key={conversation.id} className={conversation.id === conversationId ? "active" : ""} onClick={() => { void loadConversation(conversation.id); setMobileSidebar(false); }}><MessageSquareText size={15} /><span>{conversation.title}</span></button>)}</nav>
      <div className="sidebar-bottom"><div className="research-tag"><span className="status-dot" /> Saved on this device</div><div className="user-profile"><span className="user-avatar"><MessageSquareText size={13} /></span><span>Local device<small>Private workspace</small></span></div></div>
    </aside>
    <section className="chat-panel">
      <header className="topbar"><button className="icon-button menu-button" aria-label="Toggle sidebar" onClick={() => { toggleSidebar(); setMobileSidebar((open) => !open); }}><Menu size={19} /></button><div className="top-title"><span>{conversations.find((item) => item.id === conversationId)?.title ?? "AskPanel"}</span></div><div className="top-actions"><button className="ask-button" onMouseDown={(event) => event.preventDefault()} onPointerDown={(event) => event.preventDefault()} onClick={askFromButton}><Sparkles size={15} /> Ask</button></div></header>
      {!conversationId ? <section className="empty-state"><span className="empty-orbit"><MessageSquareText size={27} /></span><span className="eyebrow">Private chat</span><h1>What are you thinking through?</h1><p>Your conversations are kept in this local workspace.</p><button className="primary-button" onClick={() => void createConversation()}>Start chatting <ArrowUp size={16} /></button></section> : <>
        <div className="message-scroller" ref={mainScroller} onScroll={(event) => { const element = event.currentTarget; element.dataset.userScrolled = element.scrollTop + element.clientHeight < element.scrollHeight - 80 ? "true" : ""; }}><div className="transcript">
          {messages.length === 0 && <div className="chat-intro"><span className="eyebrow">AskPanel / Main chat</span><h1>What would you like<br />to work through?</h1><p>Ask a question to start a conversation.</p></div>}
          {messages.map((message) => <article key={message.id} className={`message-row ${message.role}`} data-message-id={message.id} data-status={message.status}><div className="message-avatar">{message.role === "assistant" ? <Sparkles size={15} /> : <MessageSquareText size={14} />}</div><div className="message-column"><div className="message-label">{message.role === "assistant" ? "AskPanel" : "You"}{message.status === "streaming" && <i className="live-dot" />}{message.role === "user" && message.status !== "streaming" && <button className="message-action" aria-label="Edit message" onClick={() => { setEditing(message.id); setEditDraft(message.content); }}><Pencil size={13} /></button>}</div>{editing === message.id ? <div className="edit-box"><textarea value={editDraft} onChange={(event) => setEditDraft(event.target.value)} /><div><button className="quiet-button" onClick={() => setEditing(null)}>Cancel</button><button className="primary-button compact" onClick={() => void saveEdit(message)}>Save &amp; resubmit <ArrowUp size={14} /></button></div></div> : <><Markdown content={message.content} threads={threads.filter((thread) => thread.parent_message_id === message.id)} onAnchor={clickAnchor} />{message.status === "error" && <div className="error-note">This reply could not be completed.</div>}</>}</div></article>)}
        </div></div>
        <form className="composer-wrap" onSubmit={sendMain}><div className="composer"><textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void sendMain(); } }} placeholder="Message AskPanel..." rows={1} /><button className="send-button" type="submit" disabled={busy || !draft.trim()} aria-label="Send message">{busy ? <span className="spinner" /> : <ArrowUp size={18} />}</button></div><div className="composer-foot"><span>Ask questions about the whole conversation, or select assistant text to branch off.</span><span><kbd>Enter</kbd> send</span></div></form>
      </>}
      {popover && selection && <div className="selection-popover" style={{ left: popover.x, top: popover.y }}><span className="selection-caption">{selection.text.slice(0, 42)}{selection.text.length > 42 ? "…" : ""}</span><button onMouseDown={(event) => event.preventDefault()} onClick={() => void openThread()}><Sparkles size={14} /> Ask Panel</button></div>}
      {chooser.length > 0 && <div className="chooser-popover"><div><b>Choose an anchored chat</b><button aria-label="Close chooser" onClick={() => setChooser([])}><X size={15} /></button></div>{chooser.map((thread) => <button key={thread.id} onClick={() => void openThread(thread)}><span>{thread.anchor.text.slice(0, 55)}</span><small>{thread.message_count} messages</small></button>)}</div>}
    </section>
    {mobileSidebar && <button className="drawer-scrim" aria-label="Close sidebar" onClick={() => setMobileSidebar(false)} />}
    <Dialog.Root open={threadOpen} onOpenChange={(open) => { setThreadOpen(open); if (!open) log("modal_close", { messages_sent: Math.floor(threadMessages.length / 2) }, activeThread?.id); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="thread-dialog"><header className="thread-header"><div><span className="eyebrow">ASKPANEL / CONTEXT BRANCH</span><Dialog.Title>Ask about this selection</Dialog.Title></div><Dialog.Close asChild><button className="icon-button" aria-label="Close AskPanel chat"><X size={18} /></button></Dialog.Close></header><div className="thread-anchor"><Quote size={14} /><p>{activeThread?.anchor.text}</p><button aria-label="Collapse selected text" onClick={(event) => event.currentTarget.parentElement?.classList.toggle("collapsed")}><ChevronDown size={16} /></button></div><div className="thread-transcript" ref={threadScroller} onScroll={(event) => { const element = event.currentTarget; threadFollowsBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48; }}>{threadMessages.map((message) => <article key={message.id} className={`thread-message ${message.role}`}><span className="thread-avatar">{message.role === "assistant" ? <Sparkles size={14} /> : <MessageSquareText size={13} />}</span><div><small>{message.role === "assistant" ? "AskPanel" : "You"}</small><Markdown content={message.content} threads={[]} onAnchor={() => undefined} /></div></article>)}</div><form className="thread-composer" onSubmit={sendThread}><textarea value={threadDraft} onChange={(event) => setThreadDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void sendThread(event); } }} placeholder="Ask a follow-up about this selection..." rows={1} /><button className="send-button" disabled={!threadDraft.trim() || threadBusy} aria-label="Send AskPanel message">{threadBusy ? <span className="spinner" /> : <ArrowUp size={18} />}</button></form></Dialog.Content></Dialog.Portal></Dialog.Root>
    {toast && <div className="toast"><span className="toast-dot" />{toast}<button aria-label="Dismiss" onClick={() => setToast(null)}><X size={14} /></button></div>}
  </main>;
}
