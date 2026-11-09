import type {FileBindings,WorkflowFilePreview} from './workflow-file-view';
import type {WorkflowDefinition} from './workflow/types';
import type {FileExportPrepared,FileExportSelection} from '../components/workflow-file-export';

/** UI operations, deliberately separate from HTTP DTOs and converter internals. */
export type WorkflowFileClient={
 maxBytes:number;
 formats:{key:string;label:string;versions:string[]}[];
 preview:(file:File,bindings:FileBindings,signal:AbortSignal)=>Promise<WorkflowFilePreview>;
 confirm:(file:File,preview:WorkflowFilePreview,bindings:FileBindings,name:string,key:string)=>Promise<WorkflowDefinition>;
 prepareExport:(selection:FileExportSelection,editor:WorkflowDefinition,signal:AbortSignal,saved?:WorkflowDefinition)=>Promise<FileExportPrepared>;
 download:(prepared:FileExportPrepared)=>void|Promise<void>;
};
