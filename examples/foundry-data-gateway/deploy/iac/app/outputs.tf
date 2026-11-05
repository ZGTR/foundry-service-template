output "service_host" {
  description = "The private name callers use; it resolves only inside the estate's private zone."
  value       = local.service_host
}

output "target_group_arn" {
  value = aws_lb_target_group.this.arn
}

output "ecs_service_name" {
  value = aws_ecs_service.this.name
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "service_security_group_id" {
  description = "The service's own group; a database stack can admit this group without adding a group to the tasks."
  value       = aws_security_group.service.id
}
