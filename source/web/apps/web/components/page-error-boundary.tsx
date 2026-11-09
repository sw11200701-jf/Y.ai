'use client';
import { Component, type ReactNode } from 'react';
import { Alert, Button, Space } from 'antd';
import { renderDiagnostic } from '@/lib/ui-diagnostics';

/** Keep a failed workspace panel from replacing the whole app with a white page. */
export default class PageErrorBoundary extends Component<{children:ReactNode}, {failed:boolean}> {
  state={failed:false};
  static getDerivedStateFromError(){return {failed:true};}
  componentDidCatch(error:Error,info:{componentStack?:string|null}){console.error('[zhangpu-ui-render]',renderDiagnostic(error,info.componentStack||''));}
  render(){return this.state.failed?<section className="panel" role="alert"><Alert type="error" showIcon message="当前页面遇到问题" description="已保存的内容仍在。可以重新加载此页面，或返回工作台继续操作。"/><Space><Button type="primary" onClick={()=>window.location.reload()}>重新加载页面</Button><Button href="/?page=workbench">返回工作台</Button></Space></section>:this.props.children;}
}
