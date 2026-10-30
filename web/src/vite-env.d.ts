/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API prefix used by every browser request; defaults to /api. */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
