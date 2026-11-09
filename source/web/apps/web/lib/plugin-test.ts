import type { Json } from './api';
export function successfulDraftTest(result?:Json){return result?.audit?.status==='succeeded' || result?.status==='succeeded';}
