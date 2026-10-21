// CMX-219 (CMX-212 P06): the host resources strip (CMX-172). `model.ts` is its other entry
// point (legacy concurrencymodel.js imports humanBytes from it).
export { Resources, resources, tickResources } from './lib/Resources.tsx';
