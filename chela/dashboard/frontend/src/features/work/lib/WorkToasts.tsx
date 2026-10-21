// The island at the end of <body> that draws the Work toasts (toasts.ts): outside #panel-work,
// so a run's toast shows on the Wall too. Click, or wait 15 s, to dismiss.
import { useStore } from '../../../shared/store/index.ts';
import { Toast, ToastProvider } from '../../../shared/ui/index.ts';
import { dismissToast, toasts } from './toasts.ts';

export function WorkToasts() {
  const items = useStore(toasts);
  return (
    <ToastProvider>
      {items.map((t) => (
        <Toast key={t.id} open title={t.title} duration={15000} dismissOnClick onOpenChange={(o) => !o && dismissToast(t.id)}>
          {t.href && (
            <a href={t.href} target="_blank" rel="noopener noreferrer">
              PR
            </a>
          )}
        </Toast>
      ))}
    </ToastProvider>
  );
}
