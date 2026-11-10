export const PASSWORD_MIN_LENGTH = 8;
export const PASSWORD_MAX_LENGTH = 128;
export const PASSWORD_MIN_MESSAGE = '密码至少需要8位';
export function newPasswordError(value?: string): string | undefined {
  if (!value || Array.from(value).length < PASSWORD_MIN_LENGTH) return PASSWORD_MIN_MESSAGE;
  if (Array.from(value).length > PASSWORD_MAX_LENGTH) return '密码最多允许128位';
}
export const newPasswordRules = [
  { required: true, message: PASSWORD_MIN_MESSAGE },
  { validator: async (_rule: unknown, value?: string) => { const error = newPasswordError(value); if (error) throw Error(error); } },
];
export function authErrorMessage(error: unknown): string {
  const value = error as { errorFields?: unknown; code?: string; message?: string; details?: {loc?: string[]; field?: string; type?: string; code?: string; context?: { min_length?: number }}[] };
  if (value?.errorFields) return '';
  if (Array.isArray(value?.details)) {
    for (const detail of value.details) {
      const field = detail.loc?.at(-1) || detail.field;
      if (field === 'password' || field === 'new_password') {
        if (detail.context?.min_length === 1) return '请输入密码';
        if (detail.type === 'string_too_long') return '密码最多允许128位';
        return PASSWORD_MIN_MESSAGE;
      }
      if (field === 'username' && detail.type === 'string_too_short') return '账号至少需要3位';
      if (field === 'username' && detail.type === 'string_too_long') return '账号最多允许64位';
    }
  }
  if (value?.code === 'VALIDATION_ERROR') return '填写的信息不符合要求，请检查表单后重试。';
  if (value?.code === 'INVALID_CREDENTIALS') return /current/i.test(value.message || '') ? '当前密码不正确' : '账号或密码不正确';
  return value?.message || '操作未完成，请重试。';
}
