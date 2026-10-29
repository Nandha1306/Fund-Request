import type { ThemeConfig } from 'antd'

/**
 * One palette for the whole product. Budget colours are fixed so a bucket looks the same
 * on every screen (dashboard, project list, project detail, ledger).
 */
export const palette = {
  ink: '#1c2430',
  muted: '#5b6675',
  subtle: '#8a94a3',
  border: '#dfe3e9',
  borderSoft: '#eceff3',
  canvas: '#f3f5f7',
  surface: '#ffffff',
  primary: '#2f5f98',
  success: '#2f7d55',
  warning: '#b7791f',
  danger: '#b83232',
  budget: {
    liquidated: '#24476f',
    outstanding: '#5b8ec7',
    reserved: '#d4a03a',
    available: '#e6eaef',
  },
} as const

const fontFamily =
  '"Segoe UI", -apple-system, BlinkMacSystemFont, "Helvetica Neue", Roboto, Arial, sans-serif'

export const theme: ThemeConfig = {
  token: {
    colorPrimary: palette.primary,
    colorInfo: palette.primary,
    colorSuccess: palette.success,
    colorWarning: palette.warning,
    colorError: palette.danger,
    colorText: palette.ink,
    colorTextSecondary: palette.muted,
    colorTextTertiary: palette.subtle,
    colorBorder: '#d4d9e0',
    colorBorderSecondary: palette.borderSoft,
    colorBgLayout: palette.canvas,
    colorLink: palette.primary,
    borderRadius: 6,
    borderRadiusLG: 8,
    fontFamily,
    fontSize: 13,
    controlHeight: 32,
    boxShadow: '0 1px 2px rgba(16, 24, 40, 0.06)',
    boxShadowSecondary: '0 8px 24px rgba(16, 24, 40, 0.12)',
  },
  components: {
    Button: { primaryShadow: 'none', defaultShadow: 'none', dangerShadow: 'none', fontWeight: 500 },
    Table: {
      headerBg: '#f7f8fa',
      headerColor: palette.muted,
      headerSplitColor: 'transparent',
      rowHoverBg: '#f6f9fc',
      borderColor: palette.borderSoft,
      cellPaddingBlockSM: 9,
      cellPaddingInlineSM: 12,
    },
    Tabs: { horizontalItemPadding: '10px 0', horizontalItemGutter: 28, inkBarColor: palette.primary },
    Tag: { defaultBg: '#f1f3f6', defaultColor: palette.muted },
    Modal: { titleFontSize: 16 },
    Drawer: { paddingLG: 20 },
    Form: { itemMarginBottom: 16, verticalLabelPadding: '0 0 6px' },
    Segmented: { itemSelectedBg: '#ffffff', trackBg: '#eceff3' },
  },
}
