// CMX-276: the dashboard's icons, by MEANING (FRONTEND_PORT_PLAN.md §2.5): a component asks
// for `icons.orchestrator`, never for a Lucide name, so a meaning is chosen once. Lucide is
// bundled by the build (no CDN). An icon is aria-hidden unless it is the only label.
import { ChevronDown, ChevronRight, Crown, Ellipsis, Eye, FlaskConical, GitPullRequest, Keyboard, Plus, Share2, TriangleAlert } from 'lucide-react';

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
} as const;
