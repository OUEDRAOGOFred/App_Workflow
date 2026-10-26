import { create } from 'zustand';
export type Tokens = {access_token:string;refresh_token:string};
const key='atelier.refresh';
export const useSession=create<{access:string|null;ready:boolean;authenticated:boolean;accept:(tokens:Tokens)=>void;clear:()=>void;initialized:()=>void}>((set)=>({
 access:null,ready:false,authenticated:false,
 accept:(t)=>{sessionStorage.setItem(key,t.refresh_token);set({access:t.access_token,authenticated:true,ready:true});},
 clear:()=>{sessionStorage.removeItem(key);set({access:null,authenticated:false,ready:true});},
 initialized:()=>set({ready:true}),
}));
export function refreshToken(){return sessionStorage.getItem(key);}
export const usePreferences=create<{dark:boolean;toggle:()=>void}>((set)=>({dark:localStorage.getItem('atelier.theme')==='dark',toggle:()=>set(s=>{localStorage.setItem('atelier.theme',s.dark?'light':'dark');return{dark:!s.dark};})}));
export const useToasts=create<{items:{id:number;text:string;kind:'error'|'success'}[];push:(text:string,kind?:'error'|'success')=>void;remove:(id:number)=>void}>((set)=>({items:[],remove:(id)=>set(s=>({items:s.items.filter(t=>t.id!==id)})),push:(text,kind='success')=>{const id=Date.now()+Math.random();set(s=>({items:[...s.items.slice(-3),{id,text,kind}]}));setTimeout(()=>set(s=>({items:s.items.filter(t=>t.id!==id)})),7000);}}));
