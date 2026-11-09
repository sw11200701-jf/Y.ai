/** Serialize one user navigation intent, and discard results from an old identity. */
export function createNavigationGate(identity:()=>number){
  let pending=false;let serial=0;
  return {
    get pending(){return pending;},
    cancel(){serial++;pending=false;},
    async run(target:string,check:()=>Promise<unknown>,commit:(target:string)=>void):Promise<boolean>{
      if(pending)return false;
      pending=true;const ticket=++serial;const epoch=identity();
      try{if(await check()!==true||ticket!==serial||epoch!==identity())return false;commit(target);return true;}
      finally{if(ticket===serial)pending=false;}
    },
  };
}
