// CMX-276: the dashboard's icons, by MEANING (FRONTEND_PORT_PLAN.md §2.5): a component asks
// for `icons.orchestrator`, never for a Lucide name, so a meaning is chosen once. Lucide is
// bundled by the build (no CDN). An icon is aria-hidden unless it is the only label.
import { ArrowUpToLine, Ban, Bell, CalendarClock, Check, ChevronDown, ChevronRight, ChevronUp, CircleDot, CircleHelp, CircleMinus, CircleSlash, Crown, Ellipsis, ExternalLink, Eye, FlaskConical, GitPullRequest, Hand, Inbox, Keyboard, Menu, Minus, OctagonAlert, Play, Plus, RefreshCw, Scale, Search, Settings, Share2, SlidersHorizontal, SquareCheck, SquareTerminal, Star, TriangleAlert, Workflow, X } from 'lucide-react';

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
  // CMX-219: the safety float, run toasts, the launcher and the gear menu
  approvals: Scale,
  accessRequest: Hand,
  blocked: Ban,
  review: Search,
  rework: RefreshCw,
  needsHuman: OctagonAlert,
  closedUnmerged: CircleSlash,
  search: Search,
  notifications: Bell,
  settings: Settings,
  menu: Menu,
  launch: Play,
  shell: SquareTerminal,
  favorite: Star,
} as const;

export type IconName = keyof typeof icons;
