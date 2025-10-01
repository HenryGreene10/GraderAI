export const API_BASE =
  import.meta?.env?.VITE_BACKEND_BASE ||
  import.meta?.env?.VITE_BACKEND_URL ||
  import.meta?.env?.VITE_API_BASE ||
  "http://localhost:8000";
