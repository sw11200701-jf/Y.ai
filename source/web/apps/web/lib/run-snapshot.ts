/** UI response isolation only. No execution or checkpoint state is stored here. */
export type RunReadTicket = Readonly<{ runId:string; identity:number; selection:number; read:number }>;

export function createRunReadGuard(identityEpoch:()=>number) {
  let selected:string|null=null;let selection=0;let read=0;
  return {
    select(runId:string|null){selected=runId;selection++;read=0;},
    invalidate(){selection++;read=0;},
    ticket():RunReadTicket|null{return selected?{runId:selected,identity:identityEpoch(),selection,read:++read}:null;},
    current(ticket:RunReadTicket){return ticket.identity===identityEpoch()&&ticket.runId===selected&&ticket.selection===selection&&ticket.read===read;},
  };
}

/** Replace a complete event response; never concatenate a resumed run's old log. */
export function runEventSnapshot<T extends {sequence:number}>(events:readonly T[]):T[]{
  const bySequence=new Map<number,T>();
  for(const event of events)if(Number.isSafeInteger(event.sequence)&&event.sequence>=0)bySequence.set(event.sequence,event);
  return [...bySequence.values()].sort((left,right)=>left.sequence-right.sequence);
}
