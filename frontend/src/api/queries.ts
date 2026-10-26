import {useQuery} from '@tanstack/react-query';
import {api} from './client';
export function useResource<T>(key:string,path=key,interval?:number){return useQuery({queryKey:[key],queryFn:({signal})=>api<T>(path,{signal}),refetchInterval:interval});}
export async function allPages<T>(path:string,signal?:AbortSignal):Promise<T[]>{const result:T[]=[];for(let offset=0;;offset+=200){const page=await api<T[]>(`${path}${path.includes('?')?'&':'?'}limit=200&offset=${offset}`,{signal});result.push(...page);if(page.length<200)return result;}}
export function useAll<T>(key:string,interval?:number){return useQuery({queryKey:[key],queryFn:({signal})=>allPages<T>(key,signal),refetchInterval:interval});}
