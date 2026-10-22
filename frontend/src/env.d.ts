interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_PUBLIC_DEMO?: string;
}
interface ImportMeta { readonly env: ImportMetaEnv }
declare module "*?url" { const url: string; export default url; }
