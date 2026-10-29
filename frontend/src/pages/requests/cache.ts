import type { QueryClient } from '@tanstack/react-query'

import type { RequestDetail } from '../../api/types'

/** After any request write: store the fresh detail and refresh everything that shows it or its money. */
export function afterRequestChange(queryClient: QueryClient, detail: RequestDetail) {
  queryClient.setQueryData(['request', detail.id], detail)
  queryClient.invalidateQueries({ queryKey: ['request', detail.id, 'history'] })
  queryClient.invalidateQueries({ queryKey: ['requests'] })
  queryClient.invalidateQueries({ queryKey: ['projects'] })
  queryClient.invalidateQueries({ queryKey: ['project', detail.project.id] })
  queryClient.invalidateQueries({ queryKey: ['notifications'] })
  queryClient.invalidateQueries({ queryKey: ['audit'] })
}
