import { useQuery } from '@tanstack/react-query'
import { Card, Tag } from 'antd'

import { api } from '../api/client'

export default function HomePage() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: async () => (await api.get<{ status: string }>('/health')).data,
  })

  return (
    <Card title="System status" style={{ maxWidth: 420 }}>
      {health.isPending && <Tag>Checking API…</Tag>}
      {health.isSuccess && <Tag color="green">API online</Tag>}
      {health.isError && <Tag color="red">API offline</Tag>}
    </Card>
  )
}
