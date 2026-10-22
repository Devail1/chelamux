// CMX-247 (CMX-212 P33): the Wall: its Gridstack grid of tiles and the single pane (it was
// static/js/terminals.js). nav.js hands it the view switch through configureWall.
export {
  absorbFreshTerminals, activePane, adoptSidebarDefault, configureWall, dropTerminalPane, focusPaneByWid, isWallVisible, label, minimizePane,
  refreshPaneLabels, renderedWids, renderTerminals, restoreFromDock, setTermMode, showPane, stepAgentMobile, termTick, type WallHost,
} from './lib/wall.ts';
