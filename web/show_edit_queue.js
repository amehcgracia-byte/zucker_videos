// Keep every draft until its save succeeds. Rendering awaits this same queue.
(function(root){
  class EditQueue {
    constructor(write){this.write=write;this.pending=new Map();this.running=Promise.resolve();}
    enqueue(id,changes){this.pending.set(id,{...this.pending.get(id),...changes});}
    flush(){
      this.running=this.running.catch(()=>{}).then(async()=>{
        const batch=[...this.pending];this.pending.clear();const errors=[];
        for(const [id,changes] of batch){
          try{await this.write(id,changes);}
          catch(error){
            // A newer draft wins over the values from the failed request.
            this.pending.set(id,{...changes,...this.pending.get(id)});
            errors.push(error.message||String(error));
          }
        }
        if(errors.length)throw new Error('Changes not saved: '+errors.join('; '));
      });
      return this.running;
    }
  }
  if(typeof module!=='undefined'&&module.exports)module.exports=EditQueue;
  else root.ShowEditQueue=EditQueue;
})(typeof window==='undefined'?{}:window);
