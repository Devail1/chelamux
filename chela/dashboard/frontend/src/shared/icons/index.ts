// CMX-276: the dashboard's icons, by MEANING (FRONTEND_PORT_PLAN.md §2.5): a component asks
// for `icons.orchestrator`, never for a Lucide name, so a meaning is chosen once. Lucide is
// bundled by the build (no CDN). An icon is aria-hidden unless it is the only label.
import {
  ArrowUpToLine, Ban, CalendarClock, Check, ChevronDown, ChevronRight, ChevronUp, Circle,
  CircleDashed, CircleDot, CircleHelp, CircleMinus, Columns3, Crown, Ellipsis, ExternalLink, Eye,
  FlaskConical, GitMerge, GitPullRequest, Inbox, Keyboard, LayoutGrid, Lock, Minus, NotebookPen,
  OctagonX, Play, Plus, RefreshCw, RotateCw, Share2, SlidersHorizontal, Snowflake, SquareCheck,
  TriangleAlert, Workflow, X,
} from 'lucide-react';

export const icons = {
  orchestrator: Crown,
  pullRequest: GitPullRequest,
  judging: FlaskConical,
  expand: ChevronRight,
  chevronDown: ChevronDown,
  more: Ellipsis,
  add: Plus,
  share: Share2,
  viewOnly: Eye,
  typing: Keyboard,
  unsandboxed: TriangleAlert,
  close: X,
  chevronUp: ChevronUp,
  keyboard: Keyboard,
  scrollback: ArrowUpToLine,
  view: SlidersHorizontal,
  wall: LayoutGrid,
  work: Columns3,
  recent: RotateCw,
  tasks: SquareCheck,
  runs: Workflow,
  schedule: CalendarClock,
  done: Check,
  skipped: CircleMinus,
  warn: TriangleAlert,
  inbox: Inbox,
  retry: RefreshCw,
  external: ExternalLink,
  ciPassing: Check,
  ciFailing: X,
  ciPending: CircleDot,
  ciNone: Minus,
  ciUnknown: CircleHelp,
  // CMX-233: the Work board's cards. A run's status pill: the word carries it, the icon agrees.
  statusOpen: Circle,
  claimed: CircleDashed,
  running: Play,
  awaitingReview: Eye,
  needsHuman: OctagonX,
  closedUnmerged: Ban,
  blockedRace: Snowflake,
  parked: Lock,
  handoff: NotebookPen,
  merged: GitMerge,
  board: LayoutGrid,
} as const;

/** A meaning in the map, for a pure model that names the icon its renderer draws. */
export type IconName = keyof typeof icons;
