import { Layout, Typography } from 'antd'
import { Route, Routes } from 'react-router-dom'

import HomePage from './pages/HomePage'

export default function App() {
  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Header style={{ display: 'flex', alignItems: 'center' }}>
        <Typography.Text strong style={{ color: '#fff', fontSize: 16 }}>
          Fund Request &amp; Liquidation
        </Typography.Text>
      </Layout.Header>
      <Layout.Content style={{ padding: 24 }}>
        <Routes>
          <Route path="/" element={<HomePage />} />
        </Routes>
      </Layout.Content>
    </Layout>
  )
}
