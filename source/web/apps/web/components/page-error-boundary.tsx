'use client';
import { Component, type ReactNode } from 'react';
import { Alert, Button, Space } from 'antd';
import { renderDiagnostic } from '@/lib/ui-diagnostics';
import { reportClientError } from '@/lib/api';
import { SupportReference } from './shared';

/** Keep a failed workspace panel from replacing the whole app with a white page. */
export default class PageErrorBoundary extends Component<{children:ReactNode}, {failed:boolean;reference?:string}> {
  state:{failed:boolean;reference?:string}={failed:false};
  private mounted=false;
  componentDidMount(){this.mounted=true;}
  componentWillUnmount(){this.mounted=false;}
  static getDerivedStateFromError(){return {failed:true};}
  componentDidCatch(error:Error,info:{componentStack?:string|null}){
    console.error('[zhangpu-ui-render]',renderDiagnostic(error,info.componentStack||''));
    void reportClientError('PAGE_RENDER_FAILED').then(reference=>{if(this.mounted&&reference)this.setState({reference});});
  }
  render(){return this.state.failed?<section className="panel" role="alert"><Alert type="error" showIcon message="当前页面遇到问题" description={<>可以重新加载此页面，或返回工作台继续操作。<SupportReference value={this.state.reference}/></>}/><Space><Button type="primary" onClick={()=>window.location.reload()}>重新加载页面</Button><Button href="/?page=workbench">返回工作台</Button></Space></section>:this.props.children;}
}
