// The page's ONE module script (CMX-215): index.html loads this build entry through the
// manifest, and it renders the whole page into #root (CMX-248). The stylesheet is the entry's.
import '../../static/style.css';
import { mountPage, start } from './boot.ts';

mountPage();
start();
