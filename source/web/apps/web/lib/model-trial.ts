import type {Json} from './api';
export type TrialReceipt={sent?:boolean;received?:boolean;elapsed?:number;answer?:string;state?:string;truncated?:boolean;valid?:boolean};
export function trialReceipt(value:Json):TrialReceipt {
 const elapsed=Number.isSafeInteger(value.elapsed_ms)&&value.elapsed_ms>=0?value.elapsed_ms:undefined;
 return {sent:typeof value.request_sent==='boolean'?value.request_sent:undefined,received:typeof value.response_received==='boolean'?value.response_received:undefined,elapsed,
  answer:typeof value.answer==='string'?value.answer:undefined,state:value.response_state,truncated:value.completion_truncated===true,valid:value.response_valid===true};
}
export function trialPassed(value:Json){return value.test_kind==='user_prompt'&&value.request_sent===true&&value.response_received===true&&value.response_valid===true;}
