// A missing protected resource can mean revoked access. Keep this separate from
// validation/CSRF failures, which must preserve the user's editable input.
export function invalidatesAccess(path:string,status:number,code?:string) {
  if(path.startsWith('/auth/'))return false;
  // These administrator tests can be rejected by network policy before any
  // credential is read. Other paths, codes and missing resources still revoke.
  if(status===403&&code==='CONNECTION_BLOCKED'&&/^\/(model-providers\/[^/?]+\/test-connection|models\/[^/?]+\/test-(?:inference|prompt))(\?|$)/.test(path))return false;
  if(status===403)return code!=='PASSWORD_CHANGE_REQUIRED'&&!/CSRF/i.test(code||'');
  return status===404&&/^\/(applications|knowledge(?:-bases)?|database(?:s)?|data-access|model-providers|models)(\/|\?|$)/.test(path);
}
