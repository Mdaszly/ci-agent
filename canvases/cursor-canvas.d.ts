declare module "cursor/canvas" {
  type CSSProperties = Record<string, string | number | undefined>;
  type ReactNode = any;

  export type CanvasHostTheme = {
    stroke: Record<string, string>;
    surface: Record<string, string>;
    text: Record<string, string>;
  };

  export function useHostTheme(): CanvasHostTheme;
  export function mergeStyle(base: CSSProperties, override?: CSSProperties): CSSProperties;

  export function Stack(props: { children?: ReactNode; gap?: number; style?: CSSProperties }): any;
  export function Row(props: {
    children?: ReactNode;
    gap?: number;
    align?: "start" | "center" | "end" | "stretch";
    justify?: "start" | "center" | "end" | "space-between";
    wrap?: boolean;
    style?: CSSProperties;
  }): any;
  export function Grid(props: {
    children?: ReactNode;
    columns: number | string;
    gap?: number;
    align?: "start" | "center" | "end" | "stretch";
    style?: CSSProperties;
  }): any;
  export function Divider(props: { style?: CSSProperties }): any;

  export function H1(props: { children?: ReactNode; style?: CSSProperties }): any;
  export function H2(props: { children?: ReactNode; style?: CSSProperties }): any;
  export function H3(props: { children?: ReactNode; style?: CSSProperties }): any;
  export function Text(props: {
    children?: ReactNode;
    tone?: "primary" | "secondary" | "tertiary" | "quaternary";
    size?: "body" | "small";
    as?: "p" | "span";
    weight?: "normal" | "medium" | "semibold" | "bold";
    italic?: boolean;
    truncate?: boolean | "start" | "end";
    style?: CSSProperties;
  }): any;
  export function Code(props: { children?: ReactNode; style?: CSSProperties }): any;
  export function Callout(props: {
    children?: ReactNode;
    tone?: "info" | "success" | "warning" | "danger";
    title?: ReactNode;
    icon?: ReactNode;
    style?: CSSProperties;
  }): any;
  export function Pill(props: {
    children?: ReactNode;
    active?: boolean;
    tone?: "info" | "success" | "warning" | "danger";
    size?: "sm" | "md";
    leadingContent?: ReactNode;
    keyboardHint?: string;
    disabled?: boolean;
    title?: string;
    style?: CSSProperties;
    onClick?: () => void;
  }): any;
  export function Stat(props: { label?: ReactNode; value?: ReactNode; tone?: "success" | "danger" | "warning" | "info"; style?: CSSProperties }): any;
  export function Table(props: {
    headers: ReactNode[];
    rows: ReactNode[][];
    columnAlign?: Array<"left" | "center" | "right" | undefined>;
    rowTone?: Array<"success" | "danger" | "warning" | "info" | "neutral" | undefined>;
    framed?: boolean;
    striped?: boolean;
    stickyHeader?: boolean;
    style?: CSSProperties;
    emptyMessage?: ReactNode;
  }): any;

  export function Card(props: {
    children?: ReactNode;
    variant?: "default" | "borderless";
    size?: "base" | "lg";
    stickyHeader?: boolean;
    collapsible?: boolean;
    defaultOpen?: boolean;
    open?: boolean;
    onOpenChange?: (open: boolean) => void;
    style?: CSSProperties;
  }): any;
  export function CardHeader(props: { children?: ReactNode; trailing?: ReactNode; style?: CSSProperties }): any;
  export function CardBody(props: { children?: ReactNode; style?: CSSProperties }): any;
}