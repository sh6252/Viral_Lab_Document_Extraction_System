import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import workerURL from "pdfjs-dist/legacy/build/pdf.worker.min.mjs?url";

function Page({ pdf, number, width, zoom, root }: { pdf: PDFDocumentProxy; number: number; width: number; zoom: number; root: HTMLDivElement | null }) {
  const box = useRef<HTMLDivElement | null>(null), canvas = useRef<HTMLCanvasElement | null>(null);
  const [visible, setVisible] = useState(false), [ratio, setRatio] = useState(Math.SQRT2), [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!box.current) return;
    if (typeof IntersectionObserver === "undefined") { setVisible(true); return; }
    const observer = new IntersectionObserver(entries => setVisible(entries[0].isIntersecting), { root, rootMargin: "400px" });
    observer.observe(box.current);
    return () => observer.disconnect();
  }, [root]);
  useEffect(() => {
    if (!visible || !canvas.current || !width) return;
    let disposed = false, task: RenderTask | undefined;
    const target = canvas.current;
    setFailed(false);
    void (async () => {
      const page = await pdf.getPage(number);
      if (disposed) return;
      const original = page.getViewport({ scale: 1 });
      setRatio(original.height / original.width);
      const viewport = page.getViewport({ scale: width * zoom / original.width });
      // Bound live canvas memory; offscreen pages release their pixel buffers.
      const pixels = Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(4_000_000 / (viewport.width * viewport.height)));
      target.width = Math.max(1, Math.floor(viewport.width * pixels));
      target.height = Math.max(1, Math.floor(viewport.height * pixels));
      task = page.render({ canvas: target, viewport, transform: [pixels, 0, 0, pixels, 0, 0] });
      await task.promise;
      if (!disposed) target.dataset.rendered = "true";
    })().catch(() => { if (!disposed) setFailed(true); });
    return () => { disposed = true; task?.cancel(); delete target.dataset.rendered; target.width = 0; target.height = 0; };
  }, [pdf, number, width, zoom, visible]);
  return <div ref={box} className="pdf-page" style={{ width: width * zoom, height: width * zoom * ratio }} data-pdf-page={number}>
    <canvas ref={canvas} role="img" aria-label={`עמוד ${number} במסמך המקור`} />
    {failed && <p role="alert">לא ניתן להציג עמוד זה. אפשר לפתוח את המקור בחלון נפרד.</p>}
  </div>;
}

/** Self-hosted PDF.js: no native-browser sidebar, CDN, upload or extraction. */
export function PdfPreview({ url, source }: { url: string; source: Blob }) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null), [failed, setFailed] = useState(false);
  const [width, setWidth] = useState(0), [zoom, setZoom] = useState(1);
  const area = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const element = area.current;
    if (!element) return;
    const measure = () => setWidth(Math.max(1, element.clientWidth - 24));
    measure();
    const observer = new ResizeObserver(measure); observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let disposed = false, destroy: (() => void) | undefined;
    setFailed(false); setPdf(null); setZoom(1);
    void (async () => {
      const library = await import("pdfjs-dist/legacy/build/pdf.mjs");
      if (disposed) return;
      library.GlobalWorkerOptions.workerSrc = workerURL;
      const data = new Uint8Array(await source.arrayBuffer());
      if (disposed) return;
      // Read the already-authorized local Blob; no blob: fetch or CSP exception is required.
      const task = library.getDocument({ data, enableXfa: false, useWasm: false, useSystemFonts: true, verbosity: 0 });
      destroy = () => { void task.destroy().catch(() => {}); };
      const loaded = await task.promise;
      if (!disposed) setPdf(loaded);
    })().catch(() => { if (!disposed) setFailed(true); });
    return () => { disposed = true; destroy?.(); };
  }, [url, source]);
  return <div className="pdf-viewer" data-source-url={url}>
    <div className="pdf-toolbar" role="group" aria-label="תצוגת PDF">
      <button type="button" className="button button-subtle" onClick={() => setZoom(1)}>התאמה לרוחב</button>
      <button type="button" className="button button-subtle" aria-label="הקטנת מסמך" disabled={zoom <= 0.5} onClick={() => setZoom(value => Math.max(0.5, value - 0.25))}>−</button>
      <span aria-live="polite"><bdi>{Math.round(zoom * 100)}%</bdi></span>
      <button type="button" className="button button-subtle" aria-label="הגדלת מסמך" disabled={zoom >= 2.5} onClick={() => setZoom(value => Math.min(2.5, value + 0.25))}>+</button>
      {pdf && <span>{pdf.numPages} עמודים</span>}
    </div>
    <div ref={area} className="pdf-scroll" role="region" aria-label="עמודי מסמך המקור" tabIndex={0}>
      {!pdf && !failed && <p role="status">טוען את תצוגת המקור…</p>}
      {failed && <p role="alert">תצוגת PDF אינה זמינה. אפשר לפתוח את המקור בחלון נפרד.</p>}
      {pdf && Array.from({ length: pdf.numPages }, (_, index) => <Page key={index} pdf={pdf} number={index + 1} width={width} zoom={zoom} root={area.current} />)}
    </div>
  </div>;
}
