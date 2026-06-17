declare module "react" {
  export type ReactNode = any;
  export type CSSProperties = Record<string, string | number | undefined>;
  export type JSXElementConstructor<P = any> = any;
  export interface Attributes {
    key?: string | number;
  }
  export interface RefAttributes<T> extends Attributes {
    ref?: any;
  }
  export interface FunctionComponent<P = {}> {
    (props: P): any;
  }
  export const Fragment: any;
  const React: any;
  export default React;
}

declare namespace JSX {
  type Element = any;
  interface IntrinsicElements {
    [elemName: string]: any;
  }
}